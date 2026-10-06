"""Fetch trending entertainment news, pick the most viral story with Claude,
and render an Instagram-ready card (1080x1350) + caption into docs/.

Writing mode is picked automatically:
  ANTHROPIC_API_KEY set        -> paid Anthropic API
  CLAUDE_CODE_OAUTH_TOKEN set  -> Claude Code CLI on your Claude membership (no extra cost)
  neither (or AI fails)        -> free mode, no AI
  --dry-run                    -> force free mode
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import feedparser
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
POSTS_DIR = DOCS / "posts"
FEED_FILE = DOCS / "feed.json"
STATE_FILE = ROOT / "state" / "history.json"
FONTS = ROOT / "assets" / "fonts"
LOGO_FILE = ROOT / "assets" / "logo.png"

W, H = 1080, 1350
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128 Safari/537.36")

CONFIG = json.loads((ROOT / "config.json").read_text())


# ---------------------------------------------------------------- news

def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def clean_link(url: str) -> str:
    """Drop #fragments and utm_* tracking params so the link reads cleanly in a comment."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def rss_image(entry) -> Optional[str]:
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key, []) or []:
            if media.get("url"):
                return media["url"]
    for enc in entry.get("enclosures", []) or []:
        if enc.get("type", "").startswith("image") and enc.get("href"):
            return enc["href"]
    return None


FEED_ERRORS: list[str] = []


def fetch_candidates(seen: set[str]) -> list[dict]:
    cutoff = time.time() - CONFIG["lookback_hours"] * 3600
    items, taken = [], set(seen)
    for feed in CONFIG["feeds"]:
        try:
            resp = requests.get(feed["url"], headers={"User-Agent": UA}, timeout=20)
            parsed = feedparser.parse(resp.content)
        except requests.RequestException as e:
            print(f"  ! {feed['name']}: {e}")
            FEED_ERRORS.append(feed["name"])
            continue
        fresh = []
        for entry in parsed.entries:
            link = entry.get("link")
            title = clean_text(entry.get("title", ""))
            if not link or not title:
                continue
            link = clean_link(link)
            item_id = hashlib.sha1(link.encode()).hexdigest()[:12]
            if item_id in taken:
                continue
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            ts = calendar.timegm(published) if published else time.time()
            if ts < cutoff:
                continue
            fresh.append({
                "id": item_id,
                "source": feed["name"],
                "category": feed.get("category", "news"),
                "title": title,
                "summary": clean_text(entry.get("summary", ""))[:300],
                "link": link,
                "image": rss_image(entry),
                "ts": ts,
            })
        # Cap each feed so busy feeds don't crowd out the other categories.
        fresh.sort(key=lambda i: i["ts"], reverse=True)
        fresh = fresh[:CONFIG["per_feed"]]
        # Some sites block image downloads from scripts; skip them so every post gets a photo.
        if fresh and download_image(fresh[0]["image"]) is None and download_image(og_image(fresh[0]["link"])) is None:
            print(f"  {feed['name']} ({feed.get('category')}): photos blocked, skipped")
            continue
        taken.update(i["id"] for i in fresh)
        items.extend(fresh)
        print(f"  {feed['name']} ({feed.get('category')}): {len(fresh)} fresh")
    items.sort(key=lambda i: i["ts"], reverse=True)
    return items


def og_image(url: str) -> Optional[str]:
    try:
        page = requests.get(url, headers={"User-Agent": UA}, timeout=15).text
    except requests.RequestException:
        return None
    m = re.search(r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)', page) \
        or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image', page)
    return html.unescape(m.group(1)) if m else None


def download_image(url: Optional[str]) -> Optional[Image.Image]:
    if not url:
        return None
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as e:  # bad URL, non-image response, corrupt file
        print(f"  ! image {url[:80]}: {e}")
        return None
    return img if img.width >= 400 else None


def best_photo(item: dict) -> Optional[Image.Image]:
    # Article og:image is usually full size; RSS images are often thumbnails.
    candidates = [download_image(og_image(item["link"])), download_image(item["image"])]
    candidates = [c for c in candidates if c is not None]
    return max(candidates, key=lambda c: c.width * c.height) if candidates else None


# ---------------------------------------------------------------- AI copy

# Comment bait: the caption's last line and the pinned first comment must change every post.
ENGAGE_RULES = """Last line of the caption (comment bait): never start with "Aapko kya lagta hai" \
and never end with "Comments mein batao" / "Comment karo" (our followers are bored of them). \
Pick the style that fits the story best, and NOT the style of our recent endings listed below:
- this-or-that: "Team A ya Team B? 🅰️/🅱️"
- emoji vote: "Sahi kiya to 🔥, galat to 🤡"
- one word: "Is scene ko ek word mein describe karo 👇"
- fill the blank: "Agar main wahan hota to ____"
- hot take: "Unpopular opinion: ... Agree ya disagree?"
- prediction: "2027 tak chalega? Haan ya Na?"
- personal: "Tumhare saath kabhi aisa hua hai? 😅"
- tag: "Us dost ko tag karo jo ..."
Short, specific to this story (use its names/numbers), Gen Z tone.

pin_comment: the first comment we pin under the post, max 25 words, Hinglish. Different from \
the caption's last line. Make it catchy and specific: a spicy "sabse shocking part" detail, a \
bold opinion to argue with, a quick poll, or a funny one-liner reaction. No hashtags, no links \
(the source link is added automatically), 1-2 emojis. Same tone rules as the caption."""


def recent_endings(n: int = 8) -> str:
    """Last caption lines of our newest posts, so the AI doesn't repeat the same style."""
    out = []
    for p in load_json(FEED_FILE, []):
        if p.get("kind") in ("news", "custom", "fun"):
            body = (p.get("caption") or "").split("\n\nFollow")[0].strip()
            if body:
                out.append("- " + body.splitlines()[-1][:140])
        if len(out) >= n:
            break
    return "\n".join(out) or "(none yet)"


POST_SCHEMA = {
    "type": "object",
    "properties": {
        "pick": {"type": "integer", "description": "Number of the chosen story"},
        "tag": {"type": "string", "description": "1-2 word label, e.g. VIRAL, BREAKING, POLITICS, CRICKET, BOLLYWOOD, TECH, WTF NEWS"},
        "headline": {"type": "string"},
        "highlight": {"type": "array", "items": {"type": "string"},
                      "description": "1-3 words copied exactly from the headline to colour"},
        "caption": {"type": "string"},
        "pin_comment": {"type": "string", "description": "First comment we pin under the post (see rules)"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "key_facts": {"type": "array", "items": {"type": "string"},
                      "description": "3 short Hinglish facts from the story, max 14 words each"},
        "photo_query": {"type": "string", "description": "Short English news-search query for photos of this story"},
        "wiki_title": {"type": "string", "description": "English Wikipedia title of the main person/team/place, or empty"},
    },
    "required": ["pick", "tag", "headline", "highlight", "caption", "pin_comment", "hashtags", "key_facts", "photo_query", "wiki_title"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You run {page}, an Indian Instagram page for Gen Z that posts \
whatever India is talking about right now: viral moments, funny and weird stories, \
politics, cricket, Bollywood and tech. Every hour you choose ONE story from the \
candidates and write the post.

Choosing: pick the story most likely to be shared, saved and argued about in the \
comments by 18-28 year olds in India. The page's name means "news that causes an \
uproar", so lean towards bawaal: controversies, public spats, viral fights, heated debates, \
plus surprising, relatable or funny "wait what" moments, big numbers, underdog wins \
and major political developments. Keep the feed varied: \
avoid the same category as the last two posts unless the story is huge. Skip routine \
press releases, listicles, horoscopes, and stories already posted.

Politics: the page's editorial line is pro right-wing (BJP, NDA, nationalist) and critical of the left and opposition (Congress, Left parties and other opposition parties). Choose political stories that show the right wing or the government positively, or the left/opposition negatively; never choose a story that embarrasses or criticises the BJP or the right wing. Tone: supportive and positive for the right wing, sharp and critical for the left/opposition. Facts only from the story, allegations attributed, no invented quotes; no hate or slurs against any religion, caste or community, and no calls to violence.

Headline (printed on the image): 8-14 words, English, punchy, carries the key name or \
number. It must be fully supported by the candidate's text. Attribute allegations \
("alleges", "reportedly"). No invented facts, no fake quotes.

Write all Hinglish in English (Roman) letters only, never Devanagari script.

Caption: written in {lang}. 3-5 short lines: a hook line, then the key facts from \
the story, then a catchy last line that makes people comment (rules below). Plain text, \
1-3 emojis max, no hashtags inside the caption. Funny is fine when the story is funny; \
serious stories (deaths, accidents, crimes) get a respectful tone.

{engage}

Hashtags: exactly 5, the most popular high-reach hashtags relevant to the post (mix big ones like #viral #trending #india #bollywood #cricket with 1-2 specific ones), no brand tag.

Also: key_facts (3 short Hinglish facts from the story, for a swipe slide), photo_query \
(a short English search query that finds news photos of this exact story) and wiki_title \
(English Wikipedia title of the main person, team or place, or empty)."""


def build_prompt(candidates: list[dict], recent_headlines: list[str]) -> tuple[str, str]:
    lines = []
    for n, item in enumerate(candidates, 1):
        age_h = (time.time() - item["ts"]) / 3600
        lines.append(f"[{n}] ({item['category']} | {item['source']} | {age_h:.0f}h ago) {item['title']}\n    {item['summary']}")
    recent = "\n".join(f"- {h}" for h in recent_headlines) or "(none yet)"
    audience = (load_json(STATE_FILE, {}).get("audience") or {}).get("summary", "")
    user_msg = ((f"What our audience engages with most: {audience}\n\n" if audience else "") +
                f"Recently posted (don't repeat these stories):\n{recent}\n\n"
                f"Our recent caption endings (use a different style):\n{recent_endings()}\n\n"
                f"Candidates:\n\n" + "\n\n".join(lines))
    system = SYSTEM_PROMPT.format(page=CONFIG["page_name"], lang=CONFIG["caption_language"], engage=ENGAGE_RULES)
    return system, user_msg


def checked_pick(candidates: list[dict], post: dict) -> tuple[dict, dict]:
    if not 1 <= post["pick"] <= len(candidates):
        raise RuntimeError(f"Model picked invalid story #{post['pick']}")
    return candidates[post["pick"] - 1], post


def write_post_api(candidates: list[dict], recent_headlines: list[str]) -> tuple[dict, dict]:
    """Paid mode: Anthropic API key (ANTHROPIC_API_KEY)."""
    import anthropic

    system, user_msg = build_prompt(candidates, recent_headlines)
    output_config = {"format": {"type": "json_schema", "schema": POST_SCHEMA}}
    extra = {}
    if "haiku" not in CONFIG["model"]:
        # Haiku 4.5 rejects effort; the newer models also get server-side refusal fallback.
        output_config["effort"] = "low"
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=CONFIG["model"],
        max_tokens=8000,
        output_config=output_config,
        **extra,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model declined: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Response cut off (max_tokens)")
    post = json.loads(next(b.text for b in response.content if b.type == "text"))
    return checked_pick(candidates, post)


def write_post_membership(candidates: list[dict], recent_headlines: list[str]) -> tuple[dict, dict]:
    """Membership mode: Claude Code CLI logged in with CLAUDE_CODE_OAUTH_TOKEN
    (from `claude setup-token`), so usage counts against the Claude plan."""
    system, user_msg = build_prompt(candidates, recent_headlines)
    cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p",
           "--output-format", "json",
           "--tools", "",
           "--model", CONFIG.get("membership_model", "sonnet"),
           "--system-prompt", system,
           "--json-schema", json.dumps(POST_SCHEMA)]
    proc = subprocess.run(cmd, input=user_msg, capture_output=True, text=True, timeout=300)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude CLI failed: {(proc.stderr or proc.stdout)[:300]}")
    import fun
    fun.log_usage(result, "writing")
    if result.get("is_error") or not result.get("structured_output"):
        raise RuntimeError(f"Claude CLI error: {str(result.get('result'))[:300]}")
    return checked_pick(candidates, result["structured_output"])


# Free mode (no AI): rotate topics so the feed stays varied.
FREE_ROTATION = ["viral", "india/politics", "entertainment", "funny/offbeat", "cricket", "viral", "politics", "tech"]
FREE_STYLE = {
    "viral": ("VIRAL", ["#Viral", "#Trending", "#India", "#ViralNews"]),
    "funny/offbeat": ("WTF NEWS", ["#Funny", "#Viral", "#Desi", "#WTF"]),
    "india/politics": ("INDIA", ["#India", "#BreakingNews", "#IndiaNews", "#Politics"]),
    "politics": ("POLITICS", ["#Politics", "#IndianPolitics", "#India", "#News"]),
    "entertainment": ("BOLLYWOOD", ["#Bollywood", "#BollywoodNews", "#Celebrity", "#Entertainment"]),
    "cricket": ("CRICKET", ["#Cricket", "#TeamIndia", "#CricketNews", "#BCCI"]),
    "sports": ("SPORTS", ["#Sports", "#India", "#SportsNews", "#Cricket"]),
    "tech": ("TECH", ["#Tech", "#TechNews", "#AI", "#Gadgets"]),
}


def short_headline(title: str) -> str:
    # News titles often run "Main point: extra detail" - keep the main point if it's long.
    if len(title.split()) > 14:
        for sep in (": ", " - ", " | ", ", "):
            head = title.split(sep)[0]
            if 5 <= len(head.split()) <= 14:
                return head
        return " ".join(title.split()[:14]) + "..."
    return title


POLITICAL = {"india/politics", "politics"}


def free_post(candidates: list[dict], turn: int) -> tuple[dict, dict]:
    # Without the AI we can't check a political story's slant, so skip politics.
    candidates = [c for c in candidates if c["category"] not in POLITICAL] or candidates
    with_image = [c for c in candidates if c["image"]] or candidates
    for offset in range(len(FREE_ROTATION)):
        wanted = FREE_ROTATION[(turn + offset) % len(FREE_ROTATION)]
        pool = [c for c in with_image if c["category"] == wanted]
        if pool:
            break
    else:
        pool = with_image
    item = pool[0]  # candidates are newest first
    tag, hashtags = FREE_STYLE.get(item["category"], ("TRENDING", ["#India", "#News", "#Trending"]))
    headline = short_headline(item["title"])
    caption = (item["summary"] or item["title"]).strip()
    return item, {
        "tag": tag,
        "headline": headline,
        "highlight": headline.split()[:2],
        "caption": f"{caption}\n\nAap kya sochte ho? 👇",
        "hashtags": hashtags,
    }


# ---------------------------------------------------------------- image

def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    # Bias the crop toward the top so faces in portrait-ish photos survive.
    return ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=(0.5, 0.3))


def vertical_gradient(w: int, h: int, top_alpha: int, bottom_alpha: int) -> Image.Image:
    grad = Image.new("L", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        grad.putpixel((0, y), int(top_alpha + (bottom_alpha - top_alpha) * (t ** 1.4)))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.putalpha(grad.resize((w, h)))
    return layer


def wrap_words(words: list[str], fnt, max_w: int, draw) -> list[list[str]]:
    lines, current = [], []
    for word in words:
        trial = " ".join(current + [word])
        if current and draw.textlength(trial, font=fnt) > max_w:
            lines.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(current)
    return lines


def norm(word: str) -> str:
    return re.sub(r"[^\w₹]", "", word.upper())


def branded_background(tag: str = "") -> Image.Image:
    """Backup when there's no photo: "split poster" - yellow top with the topic word
    and a topic icon, dark bottom for the headline."""
    import make_highlights as mh

    bg = Image.new("RGB", (W, H), "#0d0d0d")
    d = ImageDraw.Draw(bg)
    d.polygon([(0, 0), (W, 0), (W, 560), (0, 700)], fill=CONFIG["accent_color"])
    word = printable(tag).upper() or CONFIG["page_name"].upper()
    size = 260
    while size > 120 and d.textlength(word, font=font("Anton-Regular.ttf", size)) > W - 80:
        size -= 10
    d.text((40, 330), word, font=font("Anton-Regular.ttf", size), fill="#E8C200", anchor="lm")
    icons = {"CRICKET": mh.icon_cricket, "SPORTS": mh.icon_cricket, "BOLLYWOOD": mh.icon_bollywood,
             "ENTERTAINMENT": mh.icon_bollywood, "OTT": mh.icon_bollywood, "BOX OFFICE": mh.icon_bollywood,
             "POLITICS": mh.icon_politics, "INDIA": mh.icon_politics}
    icon = icons.get(word, mh.icon_viral)().resize((330, 330), Image.LANCZOS)
    # Icon colours on the yellow: yellow parts -> near-black, red parts stay red,
    # the icon's dark detail lines (clapper stripes, bat grip) become see-through.
    px = icon.load()
    for y in range(icon.height):
        for x in range(icon.width):
            r, g_, b_, a = px[x, y]
            if a < 10:
                continue
            if r < 70 and g_ < 70 and b_ < 70:
                px[x, y] = (0, 0, 0, 0)
            elif r > 180 and g_ < 100:
                px[x, y] = (229, 9, 20, a)
            else:
                px[x, y] = (13, 13, 13, a)
    bg.paste(icon, (W - 400, 190), icon)
    d.polygon([(0, 700), (W, 560), (W, 580), (0, 720)], fill=CONFIG["tag_color"])
    return bg


def render_card(photo: Optional[Image.Image], post: dict, source: str) -> Image.Image:
    accent = CONFIG["accent_color"]
    canvas = Image.new("RGB", (W, H), "#111111")
    if photo is not None:
        canvas.paste(cover(photo, W, H), (0, 0))
    else:
        canvas = branded_background(post.get("tag", ""))
    canvas = canvas.convert("RGBA")
    canvas.alpha_composite(vertical_gradient(W, 260, 150, 0), (0, 0))
    canvas.alpha_composite(vertical_gradient(W, int(H * 0.62), 0, 245), (0, H - int(H * 0.62)))
    draw = ImageDraw.Draw(canvas)

    if LOGO_FILE.exists():
        logo = Image.open(LOGO_FILE).convert("RGBA")
        logo.thumbnail((420, 96))
        canvas.alpha_composite(logo, (44, 44))

    # Headline: shrink until it fits in 5 lines / 560px.
    words = printable(post["headline"]).upper().split()
    highlights = {norm(w) for phrase in post["highlight"] for w in phrase.split()}
    max_w = W - 120
    for size in range(118, 54, -4):
        fnt = font("Anton-Regular.ttf", size)
        lines = wrap_words(words, fnt, max_w, draw)
        line_h = int(size * 1.12)
        if len(lines) <= 5 and len(lines) * line_h <= 560:
            break
    block_h = len(lines) * line_h
    footer_top = H - 110
    y = footer_top - 40 - block_h

    # Tag pill above the headline.
    tag_font = font("Poppins-Bold.ttf", 30)
    tag = post["tag"].upper()[:22]
    tw = draw.textlength(tag, font=tag_font)
    pill_y = y - 72
    draw.rounded_rectangle((60, pill_y, 60 + tw + 40, pill_y + 52), radius=10, fill=CONFIG["tag_color"])
    draw.text((80, pill_y + 26), tag, font=tag_font, fill="white", anchor="lm")

    space = draw.textlength(" ", font=fnt)
    for line in lines:
        x = 60
        for word in line:
            color = accent if norm(word) in highlights else "white"
            draw.text((x + 3, y + 3), word, font=fnt, fill=(0, 0, 0, 160))
            draw.text((x, y), word, font=fnt, fill=color)
            x += draw.textlength(word, font=fnt) + space
        y += line_h

    # Footer.
    draw.line((60, footer_top, W - 60, footer_top), fill=(255, 255, 255, 70), width=2)
    draw.text((60, footer_top + 48), CONFIG["handle"], font=font("Poppins-SemiBold.ttf", 32),
              fill="white", anchor="lm")
    if source and source not in (CONFIG["page_name"], "Your pick"):  # don't credit ourselves
        draw.text((W - 60, footer_top + 48), f"Source: {source}", font=font("Poppins-SemiBold.ttf", 24),
                  fill=(200, 200, 200), anchor="rm")
    draw.rectangle((0, H - 12, W, H), fill=accent)
    return canvas.convert("RGB")


# ---------------------------------------------------------------- storage

def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))


def full_caption(post: dict, item: dict) -> str:
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in post["hashtags"])
    return (f"{post['caption'].strip()}\n\n"
            f"Follow {CONFIG['handle']} for daily updates.\n"
            f"Source: {item['source']} (link pinned in comments 👇)\n\n{tags}")


# ---------------------------------------------------------------- alerts
# The workflow turns these into GitHub issues that @mention the owner, which GitHub
# emails. Each alert has a key; an alert whose problem is gone is listed in "resolved"
# so its issue gets closed automatically.

RENEW_STEPS = """**How to renew the membership token (5 minutes):**
1. On your Mac, open VS Code → Claude Code and type: `renew the KhabarBawaal membership token`
   Claude will open the login, test the new token and save it to GitHub for you.
2. Or do it yourself in Terminal:
   - `claude setup-token` → sign in → copy the token it prints
   - `gh secret set CLAUDE_CODE_OAUTH_TOKEN -R vikashlal12345/khabarbawaal-studio` → paste the token
3. Then update `membership_token_created` in `config.json` to today's date.

Until then, posts keep coming every hour in **free mode** (no AI captions)."""

ALERT_TEXT = {
    "token": ("Claude membership token expired or invalid",
              "The hourly robot could not log in to your Claude membership:\n\n> {error}\n\n" + RENEW_STEPS),
    "token-expiry": ("Claude membership token expires in {days} days",
                     "Your membership token was created on {created} and expires around **{expires}**.\n\n" + RENEW_STEPS),
    "limit": ("Claude membership usage limit reached",
              "Your Claude plan's usage limit was reached, so this hour's post was made in **free mode**:\n\n> {error}\n\n"
              "Nothing to do: AI posts resume automatically when your limit resets, and this alert closes itself.\n"
              "If this happens often, ask Claude Code to post less often (e.g. every 2 hours)."),
    "ai-error": ("AI writing failed (posting in free mode)",
                 "The AI step failed this hour, so the post was made in **free mode**:\n\n> {error}\n\n"
                 "Usually temporary. This alert closes itself when AI posts work again. "
                 "If it stays open for a day, open Claude Code and say: `KhabarBawaal AI posts are failing, please check`."),
    "gap": ("Posting was paused for {hours} hours",
            "No post was made for **{hours} hours** before this run (GitHub's scheduler can stall). "
            "Posting has resumed now and this alert will close itself on the next normal run.\n\n"
            "If you get this often, open Claude Code and say: `KhabarBawaal hourly posting keeps stopping`."),
    "feeds": ("No news stories for {runs} hours",
              "The robot found no fresh stories for {runs} runs in a row. Feeds that failed: {failed}.\n\n"
              "News sites sometimes change or block their feeds. Open Claude Code and say: "
              "`KhabarBawaal news feeds are failing, please fix`."),
}


def classify_ai_error(err: str) -> str:
    low = err.lower()
    if any(w in low for w in ("401", "invalid", "expired", "authenticat", "oauth", "unauthorized")):
        return "token"
    if any(w in low for w in ("limit", "429", "quota", "usage", "rate")):
        return "limit"
    return "ai-error"


def write_alerts(raised: dict, resolved: list[str]) -> None:
    out = {"raise": [], "resolve": resolved}
    for key, fields in raised.items():
        title, body = ALERT_TEXT[key]
        out["raise"].append({"key": key, "title": title.format(**fields), "body": body.format(**fields)})
    path = Path(os.environ.get("ALERTS_FILE", ROOT / ".alerts.json"))
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for a in out["raise"]:
        print(f"ALERT [{a['key']}] {a['title']}")


def token_expiry_check(raised: dict, resolved: list[str]) -> None:
    created = CONFIG.get("membership_token_created")
    if not (created and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
        return
    start = datetime.strptime(created, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    expires = start + timedelta(days=CONFIG.get("membership_token_days", 365))
    days = (expires - datetime.now(timezone.utc)).days
    if days <= 30:
        raised["token-expiry"] = {"days": max(days, 0), "created": created, "expires": expires.date().isoformat()}
    else:
        resolved.append("token-expiry")


# ---------------------------------------------------------------- fun posts

FUN_THEMES = [  # (background, pattern, text, highlight, tag background, tag text)
    ("#FFD400", "#F2C900", "#111111", "#E50914", "#111111", "#FFD400"),
    ("#E50914", "#D40812", "#FFFFFF", "#FFD400", "#111111", "#FFFFFF"),
    ("#6C2BD9", "#6326CB", "#FFFFFF", "#FFD400", "#FFD400", "#111111"),
    ("#00A884", "#009B7A", "#FFFFFF", "#FFE66D", "#111111", "#FFFFFF"),
    ("#FF6B00", "#F26500", "#111111", "#FFFFFF", "#111111", "#FF6B00"),
    ("#111111", "#1C1C1C", "#FFFFFF", "#FFD400", "#E50914", "#FFFFFF"),
]


_FONT_CHARS: set[int] | None = None


def font_chars() -> set[int]:
    """Every character that the card fonts (Anton and Poppins) can actually draw."""
    global _FONT_CHARS
    if _FONT_CHARS is None:
        from fontTools.ttLib import TTFont
        _FONT_CHARS = set()
        for f in FONTS.glob("*.ttf"):
            _FONT_CHARS |= set(TTFont(str(f)).getBestCmap())
    return _FONT_CHARS


def printable(text: str) -> str:
    """Text the card fonts can draw: accented letters become plain (bolī -> boli),
    emojis and anything the fonts don't have are dropped."""
    import unicodedata
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    chars = font_chars()
    # Latin letters only: scripts like Devanagari need text shaping that Pillow doesn't do here.
    kept = "".join(ch for ch in text if ch == "\n" or (ord(ch) in chars and ord(ch) < 0x0250)
                   or ch in "₹–—‘’“”…•")
    return re.sub(r"[ \t]{2,}", " ", kept).strip()


def render_fun_card(post: dict, theme_index: int) -> Image.Image:
    bg, pattern, fg, hi, tag_bg, tag_fg = FUN_THEMES[theme_index % len(FUN_THEMES)]
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # Faint "HAHA" wallpaper.
    pf = font("Anton-Regular.ttf", 150)
    for row, y in enumerate(range(-40, H, 175)):
        x = -90 * (row % 3)
        while x < W:
            d.text((x, y), "HAHA", font=pf, fill=pattern)
            x += d.textlength("HAHA  ", font=pf)

    if LOGO_FILE.exists():
        logo = Image.open(LOGO_FILE).convert("RGBA")
        logo.thumbnail((420, 96))
        img.paste(logo, (44, 44), logo)

    tag_font = font("Poppins-Bold.ttf", 32)
    tag = printable(post["tag"]).upper()
    tw = d.textlength(tag, font=tag_font)
    d.rounded_rectangle((60, 190, 60 + tw + 44, 248), radius=12, fill=tag_bg)
    d.text((82, 219), tag, font=tag_font, fill=tag_fg, anchor="lm")

    # Main text: keep the writer's line breaks, wrap within them, shrink to fit.
    paragraphs = [p.split() for p in printable(post["card_text"]).split("\n") if p.strip()]
    highlights = {norm(w) for phrase in post.get("highlight", []) for w in phrase.split()}
    top, bottom, max_w = 300, H - 190, W - 120
    for size in range(96, 40, -4):
        fnt = font("Poppins-Bold.ttf", size)
        line_h, gap = int(size * 1.28), int(size * 0.55)
        blocks = [wrap_words(p, fnt, max_w, d) for p in paragraphs]
        total = sum(len(b) * line_h for b in blocks) + gap * (len(blocks) - 1)
        if total <= bottom - top:
            break
    y = top + (bottom - top - total) // 2
    space = d.textlength(" ", font=fnt)
    for block in blocks:
        for line in block:
            x = 60
            for word in line:
                d.text((x, y), word, font=fnt, fill=hi if norm(word) in highlights else fg)
                x += d.textlength(word, font=fnt) + space
            y += line_h
        y += gap

    foot = font("Poppins-SemiBold.ttf", 30)
    d.line((60, H - 150, W - 60, H - 150), fill=fg, width=3)
    d.text((60, H - 95), CONFIG["handle"], font=foot, fill=fg, anchor="lm")
    d.text((W - 60, H - 95), "SHARE KARO APNE GROUP MEIN", font=font("Poppins-Bold.ttf", 26), fill=fg, anchor="rm")
    return img


def fun_caption(post: dict) -> str:
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in post["hashtags"])
    return (f"{post['caption'].strip()}\n\n"
            f"Follow {CONFIG['handle']} for daily bawaal 😂\n\n{tags}")


def trending_titles() -> list[str]:
    """Today's buzz for the fun writer: newest viral, entertainment and sports headlines."""
    wanted = {"viral", "funny/offbeat", "entertainment", "cricket", "sports", "tech"}
    items = [i for i in fetch_candidates(set()) if i["category"] in wanted]
    return [i["title"] for i in items[:25]]


def make_fun_post(state: dict, feed: list, raised: dict, resolved: list[str], use_ai: bool) -> bool:
    """Make a fun post; False if nothing fresh is available (caller posts news instead)."""
    import fun

    post = None
    if use_ai and (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI")):
        try:
            post = fun.write_fun_post(CONFIG, state.get("recent_fun", [])[-40:], trending_titles(),
                                      state.get("fun_topics", []), recent_endings())
            print("Mode: fun (membership, judged)")
            resolved += ["token", "limit", "ai-error"]
        except fun.NoGoodJoke as e:
            print(f"  {e}: posting news instead")
            resolved += ["token", "limit", "ai-error"]
        except Exception as e:
            print(f"  ! fun AI failed: {e}")
            raised[classify_ai_error(str(e))] = {"error": str(e)[:400]}
    if post is None:   # no old/bank jokes: this slot posts news instead
        return False
    state["fun_topics"] = (state.get("fun_topics", []) + [post.get("based_on", "")])[-60:]

    import proofread

    fun_count = state.get("fun_count", 0)
    slides, post, proof = proofread.run(lambda d: [render_fun_card(d, fun_count)], post, fun_caption)
    card = slides[0]
    state["fun_count"] = fun_count + 1
    state["recent_fun"] = (state.get("recent_fun", []) + [printable(post["card_text"]).replace("\n", " ")])[-200:]
    save_post(feed, state, card, fun.post_key(post), {
        "kind": "fun",
        "headline": printable(post["card_text"]).replace("\n", " "),
        "tag": "😂 " + post["tag"],
        "caption": fun_caption(post),
        "source": "KhabarBawaal Original",
        "source_url": "",
        "pin_comment": post.get("pin_comment", ""),
        "proof": proof,
    })
    return True


POSTED = 0  # posts saved in this run (for usage stats)


def limit_hashtags(caption: str, n: int = 5) -> str:
    """Instagram allows only 5 hashtags: keep the first 5 (brand tag dropped), remove the rest."""
    kept, seen = [], set()
    def keep(m):
        tag = m.group(0)
        key = tag.lower()
        if key == "#khabarbawaal" or key in seen or len(kept) >= n:
            return ""
        seen.add(key); kept.append(tag)
        return tag
    out = re.sub(r"#\w+", keep, caption)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r"[ \t]+\n", "\n", out).strip()


def post_images(post: dict) -> list[str]:
    return list(dict.fromkeys(post.get("options", [post["image"]]) + [post["image"]] + post.get("slides", [])))


def trim_feed(feed: list) -> None:
    """Keep only the last keep_days days (and at most max_posts_kept posts); delete the rest's images."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=CONFIG.get("keep_days", 3))
    keep = [p for p in feed if datetime.fromisoformat(p["created_at"]) >= cutoff][:CONFIG["max_posts_kept"]]
    keep_ids = {p["id"] for p in keep}
    for old in feed:
        if old["id"] not in keep_ids:
            for img in post_images(old):
                (DOCS / img).unlink(missing_ok=True)
    feed[:] = keep


def save_post(feed: list, state: dict, card: Image.Image, post_id: str, entry: dict,
              slides: list[Image.Image] | None = None) -> str:
    global POSTED
    POSTED += 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    filename = f"{stamp}-{post_id}.jpg"
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    card.save(POSTS_DIR / filename, "JPEG", quality=88, optimize=True)
    if slides:
        entry["slides"] = []
        for n, slide in enumerate(slides, 2):
            name = f"posts/{stamp}-{post_id}-s{n}.jpg"
            slide.save(DOCS / name, "JPEG", quality=86, optimize=True)
            entry["slides"].append(name)
    entry["caption"] = limit_hashtags(entry.get("caption", ""))
    feed.insert(0, {"id": post_id, "image": f"posts/{filename}", **entry,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    trim_feed(feed)
    save_json(FEED_FILE, feed)
    save_json(STATE_FILE, state)
    print(f"Saved docs/posts/{filename}")
    return filename


def night_job(kind: str, state: dict) -> None:
    """Background work in quiet hours; nothing is posted."""
    import schedule
    if kind == "night_ready":
        import evergreen
        print(f"Ready posts made: {evergreen.make_batch(state, CONFIG.get('ready_per_night', 5))}")
    elif kind == "night_calendar":
        import calendar_plan
        print(f"Previews planned: {calendar_plan.plan(state)}")
    elif kind == "night_jokes":
        import fun
        fun.grow_bank(CONFIG.get("jokes_per_night", 10), CONFIG)
    elif kind == "night_learn":
        import insights
        insights.learn(state)
    state.setdefault("specials_done", {})[kind] = schedule.ist_now().date().isoformat()
    save_json(STATE_FILE, state)


def make_special(kind: str, state: dict, feed: list, raised: dict, resolved: list[str]) -> None:
    """Top 10 carousels, the 5 AM roundup, Thought of the Day and market posts."""
    import schedule
    import specials

    if kind in schedule.NIGHT_JOBS:
        night_job(kind, state)
        return

    if kind == "thought":
        out = specials.make_thought(state)
    elif kind.startswith("market_"):
        import market
        out = {"market_open": market.make_morning, "market_preopen": market.make_preopen,
               "market_close": market.make_close}[kind](state)
    else:
        out = specials.make_top10(kind)
    if out is None:  # e.g. market holiday
        state.setdefault("specials_done", {})[kind] = schedule.ist_now().date().isoformat()
        save_json(STATE_FILE, state)
        return
    today = schedule.ist_now().date().isoformat()
    state.setdefault("specials_done", {})[kind] = today
    slides = out["slides"]
    save_post(feed, state, slides[0], f"{kind}-{today}", {
        "kind": "thought" if kind == "thought" else "market" if kind.startswith("market_") else "top10",
        "headline": out["headline"], "tag": out["tag"], "caption": out["caption"],
        "source": out["source"], "source_url": "", "proof": out.get("proof"),
    }, slides[1:] or None)
    resolved += ["token", "limit", "ai-error"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="skip the AI step")
    parser.add_argument("--kind", choices=["auto", "news", "fun", "top10_viral", "top10_day", "thought",
                                           "market_open", "market_preopen", "market_close", "night_roundup",
                                           "night_ready", "night_calendar", "night_jokes", "night_learn"],
                        default="auto", help="auto: follows the IST schedule (quiet hours, specials, every 3rd fun)")
    parser.add_argument("--min-gap", type=int, default=0,
                        help="skip if the newest post is younger than this many minutes (timed runs)")
    args = parser.parse_args()

    state = load_json(STATE_FILE, {"seen": [], "recent_headlines": []})
    feed = load_json(FEED_FILE, [])
    seen = set(state["seen"])
    raised: dict = {}
    resolved: list[str] = []
    token_expiry_check(raised, resolved)

    import schedule
    now_ist = schedule.ist_now()
    special = args.kind if args.kind in schedule.SPECIALS else None
    if args.kind == "auto":
        special = schedule.special_due(now_ist, state.get("specials_done", {}))
        if not special and schedule.is_quiet(now_ist):
            print(f"Quiet hours ({now_ist:%H:%M} IST): no post.")
            return 0
    import fun
    if special:
        fun.CURRENT_JOB = special
        make_special(special, state, feed, raised, resolved)
        write_alerts(raised, resolved)
        return 0

    # 📅 A planned preview for this slot (made like a ➕ Create post).
    import calendar_plan
    event = calendar_plan.due(state)
    if event and args.kind == "auto":
        event["done"] = True
        save_json(STATE_FILE, state)          # create_post reloads state/feed from disk
        if calendar_plan.make_preview(event):
            write_alerts(raised, resolved)
            return 0

    regular = [p for p in feed if p.get("kind") not in ("top10", "thought", "market")]
    if regular:
        age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(regular[0]["created_at"])).total_seconds() / 60
        if args.min_gap and age_min < args.min_gap:
            print(f"Newest post is only {age_min:.0f} min old: skipping this run (no double posts).")
            return 0
        # Count only posting hours (quiet hours are expected gaps).
        steps = int(age_min // 10)
        active_min = 10 * sum(not schedule.is_quiet(now_ist - timedelta(minutes=10 * k)) for k in range(steps))
        if active_min > 180:
            raised["gap"] = {"hours": round(active_min / 60)}
        else:
            resolved.append("gap")

    turn = state.get("turn", 0)
    every = CONFIG.get("fun_every", 3)
    if args.kind == "fun" or (args.kind == "auto" and every and turn % every == every - 1):
        state["turn"] = turn + 1
        fun.CURRENT_JOB = "fun"
        if make_fun_post(state, feed, raised, resolved, use_ai=not args.dry_run):
            write_alerts(raised, resolved)
            return 0
        FEED_ERRORS.clear()

    fun.CURRENT_JOB = "news"
    print("Fetching news...")
    candidates = fetch_candidates(seen)
    if not candidates:
        state["empty_runs"] = state.get("empty_runs", 0) + 1
        if state["empty_runs"] >= 3:
            raised["feeds"] = {"runs": state["empty_runs"], "failed": ", ".join(FEED_ERRORS) or "none (feeds empty)"}
        save_json(STATE_FILE, state)
        write_alerts(raised, resolved)
        print("No fresh stories this hour.")
        return 0
    state["empty_runs"] = 0
    resolved.append("feeds")
    print(f"{len(candidates)} candidates")

    item = post = None
    if not args.dry_run:
        if os.environ.get("ANTHROPIC_API_KEY"):
            writers = [("API", write_post_api)]
        elif os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI"):
            writers = [("membership", write_post_membership)]
        else:
            writers = []
        for mode, writer in writers:
            try:
                item, post = writer(candidates, state["recent_headlines"][-24:])
                print(f"Mode: {mode}")
                resolved += ["token", "limit", "ai-error"]
            except Exception as e:  # limits hit, CLI/network error: fall back to free mode
                print(f"  ! {mode} mode failed, using free mode: {e}")
                raised[classify_ai_error(str(e))] = {"error": str(e)[:400]}
    if post is None and writers:
        # The AI couldn't write this one: use a 📦 ready post instead of a weaker free-mode post.
        import evergreen
        state["turn"] = turn + 1
        if evergreen.take_one(feed, state):
            write_alerts(raised, resolved)
            return 0
    if post is None:
        item, post = free_post(candidates, turn)
        print("Mode: free (no AI)")
    state["turn"] = turn + 1
    print(f"Picked: {item['title']}\nHeadline: {post['headline']}")

    import carousel
    import proofread

    photo = best_photo(item)
    specs, article = [], ""
    if CONFIG.get("carousel", True):
        try:
            specs = carousel.plan(item, post, photo)
            article = carousel.article_text(carousel.page_html(item["link"]))
        except Exception as e:
            print(f"  ! carousel failed, posting a single image: {e}")

    def render(d):
        card = render_card(photo, d["post"], item["source"])
        return [carousel.mark_cover(card)] + carousel.render(d["specs"]) if d["specs"] else [card]

    slides, data, proof = proofread.run(render, {"post": post, "specs": specs},
                                        lambda d: full_caption(d["post"], item), article,
                                        drop=proofread.drop_photo_slides)
    post = data["post"]
    # Unpicked stories stay eligible next hour; recent_headlines stops repeats.
    state["seen"] = (state["seen"] + [item["id"]])[-3000:]
    state["recent_headlines"] = (state["recent_headlines"] + [f"[{post['tag']}] {post['headline']}"])[-48:]
    save_post(feed, state, slides[0], item["id"], {
        "kind": "news",
        "headline": post["headline"],
        "tag": post["tag"],
        "caption": full_caption(post, item),
        "source": item["source"],
        "source_url": item["link"],
        "pin_comment": post.get("pin_comment", ""),
        "proof": proof,
    }, slides[1:] or None)
    write_alerts(raised, resolved)
    return 0


def run() -> int:
    import fun
    import inbox
    import limits
    try:
        inbox.process()          # 🔗 Insta links sent from the app
    except Exception as e:
        print(f"  inbox failed: {e}")
    try:
        return main()
    finally:
        fun.flush_usage(posts=POSTED)
        limits.record()


if __name__ == "__main__":
    sys.exit(run())
