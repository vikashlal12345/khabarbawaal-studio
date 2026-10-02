"""Fetch trending entertainment news, pick the most viral story with Claude,
and render an Instagram-ready card (1080x1350) + caption into docs/.

Run:  python generate.py            (needs ANTHROPIC_API_KEY)
      python generate.py --dry-run  (no AI: uses the newest story as-is)
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
import sys
import time
from datetime import datetime, timezone
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


def rss_image(entry) -> Optional[str]:
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key, []) or []:
            if media.get("url"):
                return media["url"]
    for enc in entry.get("enclosures", []) or []:
        if enc.get("type", "").startswith("image") and enc.get("href"):
            return enc["href"]
    return None


def fetch_candidates(seen: set[str]) -> list[dict]:
    cutoff = time.time() - CONFIG["lookback_hours"] * 3600
    items, taken = [], set(seen)
    for feed in CONFIG["feeds"]:
        try:
            resp = requests.get(feed["url"], headers={"User-Agent": UA}, timeout=20)
            parsed = feedparser.parse(resp.content)
        except requests.RequestException as e:
            print(f"  ! {feed['name']}: {e}")
            continue
        fresh = []
        for entry in parsed.entries:
            link = entry.get("link")
            title = clean_text(entry.get("title", ""))
            if not link or not title:
                continue
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

POST_SCHEMA = {
    "type": "object",
    "properties": {
        "pick": {"type": "integer", "description": "Number of the chosen story"},
        "tag": {"type": "string", "description": "1-2 word label, e.g. VIRAL, BREAKING, POLITICS, CRICKET, BOLLYWOOD, TECH, WTF NEWS"},
        "headline": {"type": "string"},
        "highlight": {"type": "array", "items": {"type": "string"},
                      "description": "1-3 words copied exactly from the headline to colour"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["pick", "tag", "headline", "highlight", "caption", "hashtags"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You run {page}, an Indian Instagram page for Gen Z that posts \
whatever India is talking about right now: viral moments, funny and weird stories, \
politics, cricket, Bollywood and tech. Every hour you choose ONE story from the \
candidates and write the post.

Choosing: pick the story most likely to be shared, saved and argued about in the \
comments by 18-28 year olds in India. The page's name means "drama alert", so lean \
towards kalesh: public spats, viral fights and arguments, controversies, heated debates, \
plus surprising, relatable or funny "wait what" moments, big numbers, underdog wins \
and major political developments. Keep the feed varied: \
avoid the same category as the last two posts unless the story is huge. Skip routine \
press releases, listicles, horoscopes, and stories already posted.

Politics: report what happened and who said it, neutrally. No opinions, no mocking \
parties or leaders, no taking sides.

Headline (printed on the image): 8-14 words, English, punchy, carries the key name or \
number. It must be fully supported by the candidate's text. Attribute allegations \
("alleges", "reportedly"). No invented facts, no fake quotes.

Caption: written in {lang}. 3-5 short lines: a hook line, then the key facts from \
the story, then one question that invites comments. Plain text, 1-3 emojis max, \
no hashtags inside the caption. Funny is fine when the story is funny; serious \
stories (deaths, accidents, crimes) get a respectful tone.

Hashtags: 6-8 relevant ones, each starting with #."""


def write_post(candidates: list[dict], recent_headlines: list[str]) -> tuple[dict, dict]:
    import anthropic

    lines = []
    for n, item in enumerate(candidates, 1):
        age_h = (time.time() - item["ts"]) / 3600
        lines.append(f"[{n}] ({item['category']} | {item['source']} | {age_h:.0f}h ago) {item['title']}\n    {item['summary']}")
    recent = "\n".join(f"- {h}" for h in recent_headlines) or "(none yet)"
    user_msg = (f"Recently posted (don't repeat these stories):\n{recent}\n\n"
                f"Candidates:\n\n" + "\n\n".join(lines))

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
        system=SYSTEM_PROMPT.format(page=CONFIG["page_name"], lang=CONFIG["caption_language"]),
        messages=[{"role": "user", "content": user_msg}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model declined: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Response cut off (max_tokens)")
    post = json.loads(next(b.text for b in response.content if b.type == "text"))
    if not 1 <= post["pick"] <= len(candidates):
        raise RuntimeError(f"Model picked invalid story #{post['pick']}")
    return candidates[post["pick"] - 1], post


def dry_run_post(candidates: list[dict]) -> tuple[dict, dict]:
    item = next((c for c in candidates if c["image"]), candidates[0])
    words = item["title"].split()
    return item, {
        "tag": "TRENDING",
        "headline": item["title"],
        "highlight": words[:2],
        "caption": item["summary"] or item["title"],
        "hashtags": ["#India", "#Viral", "#NewsUpdate"],
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


def render_card(photo: Optional[Image.Image], post: dict, source: str) -> Image.Image:
    accent = CONFIG["accent_color"]
    canvas = Image.new("RGB", (W, H), "#111111")
    if photo is not None:
        canvas.paste(cover(photo, W, H), (0, 0))
    else:
        canvas = Image.new("RGB", (W, H), "#1a1a2e")
    canvas = canvas.convert("RGBA")
    canvas.alpha_composite(vertical_gradient(W, 260, 150, 0), (0, 0))
    canvas.alpha_composite(vertical_gradient(W, int(H * 0.62), 0, 245), (0, H - int(H * 0.62)))
    draw = ImageDraw.Draw(canvas)

    if LOGO_FILE.exists():
        logo = Image.open(LOGO_FILE).convert("RGBA")
        logo.thumbnail((420, 96))
        canvas.alpha_composite(logo, (44, 44))

    # Headline: shrink until it fits in 5 lines / 560px.
    words = post["headline"].upper().split()
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
            f"Source: {item['source']}\n\n{tags}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="skip the AI step")
    args = parser.parse_args()

    state = load_json(STATE_FILE, {"seen": [], "recent_headlines": []})
    feed = load_json(FEED_FILE, [])
    seen = set(state["seen"])

    print("Fetching news...")
    candidates = fetch_candidates(seen)
    if not candidates:
        print("No fresh stories this hour.")
        return 0
    print(f"{len(candidates)} candidates")

    if args.dry_run:
        item, post = dry_run_post(candidates)
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set: add it as a repo secret. Skipping this run.")
        return 0
    else:
        item, post = write_post(candidates, state["recent_headlines"][-24:])
    print(f"Picked: {item['title']}\nHeadline: {post['headline']}")

    card = render_card(best_photo(item), post, item["source"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    filename = f"{stamp}-{item['id']}.jpg"
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    card.save(POSTS_DIR / filename, "JPEG", quality=88, optimize=True)

    feed.insert(0, {
        "id": item["id"],
        "image": f"posts/{filename}",
        "headline": post["headline"],
        "tag": post["tag"],
        "caption": full_caption(post, item),
        "source": item["source"],
        "source_url": item["link"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    for old in feed[CONFIG["max_posts_kept"]:]:
        (DOCS / old["image"]).unlink(missing_ok=True)
    feed = feed[:CONFIG["max_posts_kept"]]

    # Unpicked stories stay eligible next hour; recent_headlines stops repeats.
    state["seen"] = (state["seen"] + [item["id"]])[-3000:]
    state["recent_headlines"] = (state["recent_headlines"] + [f"[{post['tag']}] {post['headline']}"])[-48:]
    save_json(FEED_FILE, feed)
    save_json(STATE_FILE, state)
    print(f"Saved docs/posts/{filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
