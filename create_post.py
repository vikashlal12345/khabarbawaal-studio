"""Make a post from news the owner sends in (a link and/or their own text).

Triggered by a GitHub issue titled "Create post" (opened from the app's ➕ button).
The AI writes the post on the membership; up to 3 related photos are searched
(the link's own photo, related news articles via Bing News, Wikipedia) and one
card is rendered per photo plus the brand banner, so the owner can pick.

    INPUT_TEXT="https://... or typed news" python create_post.py
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlsplit

import requests
from PIL import Image

import fun
import generate as g

import status

FALLBACK = []   # why a 🎬 Reel became a normal post (shown with the ✅ in the app)


def note(text: str, n: int) -> None:
    """Progress line for the app, only for ➕ Create requests (make() also serves 📅 previews)."""
    if status.JOB["job"] == "create":
        status.step(text, n)


def ready(text: str, post_id: str) -> None:
    if status.JOB["job"] == "create":
        status.done((f"Ready (as a normal post: {FALLBACK[0]}): " if FALLBACK else "Ready: ") + text[:90], post=post_id)


def failed(reason: str) -> None:
    if status.JOB["job"] == "create":
        status.fail(reason)


CUSTOM_POST = {
    "type": "object",
    "properties": {
        "tag": {"type": "string", "description": "1-2 word label, e.g. BREAKING, VIRAL, POLITICS, CRICKET, BOLLYWOOD, TECH"},
        "headline": {"type": "string"},
        "highlight": {"type": "array", "items": {"type": "string"},
                      "description": "1-3 words copied exactly from the headline to colour"},
        "caption": {"type": "string"},
        "pin_comment": {"type": "string", "description": "First comment we pin under the post (see rules)"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "key_facts": {"type": "array", "items": {"type": "string"},
                      "description": "3 short Hinglish facts from the story, max 14 words each"},
        "photo_query": {"type": "string", "description": "Short English news-search query to find photos of this story"},
        "wiki_title": {"type": "string", "description": "English Wikipedia article title of the main person, team or place, or empty"},
    },
    "required": ["tag", "headline", "highlight", "caption", "pin_comment", "hashtags", "key_facts", "photo_query", "wiki_title"],
    "additionalProperties": False,
}

CUSTOM_SYSTEM = g.SYSTEM_PROMPT.split("Choosing:")[0] + """The page owner has sent you ONE story to post \
(a link's article text and/or their own words). Write the post for it.
""" + "Politics:" + g.SYSTEM_PROMPT.split("Politics:")[1] + """

Also give photo_query (a short English search query that finds news photos of this exact \
story) and wiki_title (the English Wikipedia title of the main person, team or place, \
or an empty string if there isn't one)."""


def meta(page: str, *names: str) -> str:
    for name in names:
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{name}["\'][^>]+content=["\']([^"\']+)', page) \
            or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{name}["\']', page)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def read_article(url: str) -> dict:
    try:
        page = requests.get(url, headers={"User-Agent": g.UA}, timeout=20).text
    except requests.RequestException as e:
        print(f"  ! could not open link: {e}")
        page = ""
    title = meta(page, "og:title", "twitter:title")
    if not title:
        m = re.search(r"<title>(.*?)</title>", page, re.S)
        title = html.unescape(m.group(1)).strip() if m else ""
    site = meta(page, "og:site_name") or urlsplit(url).netloc.removeprefix("www.")
    return {"url": g.clean_link(url), "title": title, "site": site,
            "description": meta(page, "og:description", "description", "twitter:description"),
            "image": meta(page, "og:image", "twitter:image")}


def bing_articles(query: str, limit: int = 6) -> list[dict]:
    """Related news articles (real link + source name) from Bing News RSS. No key needed."""
    url = f"https://www.bing.com/news/search?q={quote(query)}&format=rss&setlang=en-IN&cc=IN"
    try:
        xml = requests.get(url, headers={"User-Agent": g.UA}, timeout=20).text
    except requests.RequestException:
        return []
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:limit]:
        link = html.unescape(re.search(r"<link>(.*?)</link>", item).group(1))
        real = parse_qs(urlsplit(link).query).get("url", [link])[0]
        src = re.search(r"<News:Source>(.*?)</News:Source>", item)
        title = re.search(r"<title>(.*?)</title>", item)
        out.append({"url": real, "site": html.unescape(src.group(1)).split(" on ")[0] if src else urlsplit(real).netloc,
                    "title": html.unescape(title.group(1)) if title else ""})
    return out


def wikipedia_photo(title: str) -> str:
    if not title:
        return ""
    api = ("https://en.wikipedia.org/w/api.php?action=query&format=json&redirects=1"
           f"&prop=pageimages&piprop=original&titles={quote(title)}")
    try:
        pages = requests.get(api, headers={"User-Agent": "KhabarBawaalStudio/1.0"}, timeout=15).json()["query"]["pages"]
    except Exception:
        return ""
    for p in pages.values():
        if p.get("original", {}).get("source"):
            return p["original"]["source"].split("?")[0]
    return ""


def fingerprint(img: Image.Image) -> bytes:
    return img.convert("L").resize((8, 8)).tobytes()


def find_photos(article: dict | None, post: dict, want: int = 3) -> list[tuple[Image.Image, str]]:
    """Up to `want` different photos, each with the site to credit."""
    found, seen = [], []

    def add(url: str, site: str) -> None:
        if len(found) >= want or not url:
            return
        img = g.download_image(url)
        if img is None or img.width < 500:
            return
        from PIL import ImageStat
        if ImageStat.Stat(img.convert("L").resize((64, 64))).stddev[0] < 38:  # flat/blank screenshot
            print(f"  skipped dull photo from {site}")
            return
        fp = fingerprint(img)
        if any(sum(abs(a - b) for a, b in zip(fp, s)) < 400 for s in seen):  # same photo again
            return
        seen.append(fp)
        found.append((img, site))
        print(f"  photo {len(found)}: {site}")

    if article:
        add(article["image"], article["site"])
    skip = urlsplit(article["url"]).netloc if article else ""
    for a in bing_articles(post["photo_query"]):
        if len(found) >= want - (1 if post.get("wiki_title") else 0):
            break
        if urlsplit(a["url"]).netloc == skip:
            continue
        add(g.og_image(a["url"]), a["site"])
    add(wikipedia_photo(post.get("wiki_title", "")), "Wikimedia Commons")
    return found


# 🎬 Your video/photos: the app sends ~6 frames joined into one picture plus the owner's context.
MEDIA_POST = {
    "type": "object",
    "properties": {
        "tag": {"type": "string", "description": "1-2 word label, e.g. VIRAL, WTF, BOLLYWOOD, CRICKET, DESI LIFE"},
        "headline": {"type": "string", "description": "Cover headline, 6-12 words"},
        "highlight": {"type": "array", "items": {"type": "string"},
                      "description": "1-3 words copied exactly from the headline to colour"},
        "overlay_text": {"type": "string",
                         "description": "Hook text the owner puts ON the video in Instagram, 3-10 words"},
        "caption": {"type": "string"},
        "pin_comment": {"type": "string", "description": "First comment we pin under the post (see rules)"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "cover_frame": {"type": "integer", "description": "Number of the most striking frame for the cover"},
    },
    "required": ["tag", "headline", "highlight", "overlay_text", "caption", "pin_comment", "hashtags", "cover_frame"],
    "additionalProperties": False,
}

MEDIA_SYSTEM = g.SYSTEM_PROMPT.split("Choosing:")[0] + """The page owner sent a video (or photos) \
from their phone with a few words of context. Open frames.jpg: it shows {n} numbered moments \
(left to right, top to bottom; 1 is the start). Write a Reel post for it.

""" + "Politics:" + g.SYSTEM_PROMPT.split("Politics:")[1].split("\n\nAlso: key_facts")[0] + """

For this Reel (these rules win over the ones above):
- Only say what is visible in the frames or written in the owner's context: no invented names, \
places, numbers or quotes. Don't name people the owner didn't name. If the context and the \
frames disagree, trust the context.
- overlay_text: the hook the owner types ON the video (Instagram text tool), 3-10 words, \
Hinglish, makes people stop scrolling and watch till the end (e.g. "Wait for the end 😭", \
"Delhi metro ka naya episode"). Max 1 emoji.
- headline: printed on the cover image, 6-12 words, punchy.
- caption: 2-4 short lines (hook, what happens, comment-bait last line).
- cover_frame: the number of the clearest, most striking frame."""

def write_post(text: str, article: dict | None) -> dict:
    parts = []
    if article:
        parts.append(f"Article ({article['site']}): {article['title']}\n{article['description']}")
    if text:
        parts.append(f"Owner's note: {text}")
    parts.append(f"Our recent caption endings (use a different style):\n{g.recent_endings()}")
    user = "\n\n".join(parts)
    system = CUSTOM_SYSTEM.format(page=g.CONFIG["page_name"], lang=g.CONFIG["caption_language"],
                                  engage=g.ENGAGE_RULES)
    return fun.claude_json(system, user, CUSTOM_POST, g.CONFIG.get("membership_model", "sonnet"))


def free_post(text: str, article: dict | None) -> dict:
    title = (article or {}).get("title") or text
    return {"tag": "BREAKING", "headline": g.short_headline(title), "highlight": title.split()[:2],
            "caption": ((article or {}).get("description") or text) + "\n\nAap kya sochte ho? 👇",
            "hashtags": ["#India", "#News", "#Trending"],
            "photo_query": title[:120], "wiki_title": ""}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def find_duplicate(feed: list, link: str, texts: list[str], days: int = 3) -> dict | None:
    """A post from the last `days` days with the same link or clearly the same story."""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    asked = [words(t) for t in texts if t]
    for p in feed:
        if datetime.fromisoformat(p["created_at"]).timestamp() < cutoff:
            continue
        if link and p.get("source_url") and g.clean_link(p["source_url"]) == link:
            return p
        have = words(p["headline"])
        for a in asked:
            if a and have and len(a & have) / min(len(a), len(have)) >= 0.6:
                return p
    return None


def ist_time(iso: str) -> str:
    from datetime import timedelta
    return (datetime.fromisoformat(iso) + timedelta(hours=5, minutes=30)).strftime("%d %b, %I:%M %p")


def caption(post: dict, source: str | None) -> str:
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in post["hashtags"])
    src = f"Source: {source} (link pinned in comments 👇)\n" if source else ""
    return f"{post['caption'].strip()}\n\nFollow {g.CONFIG['handle']} for daily updates.\n{src}\n{tags}"


def main() -> int:
    raw = os.environ.get("INPUT_TEXT", "").strip()
    reel_wanted = os.environ.get("FORMAT", "post") == "reel"
    ts = os.environ.get("CREATE_TS", "") or os.environ.get("ISSUE", "")
    if not raw:
        print("No input given.")
        return 1
    # Requests from the app's inbox (not a GitHub issue) must carry the owner's one-time code.
    if os.environ.get("REQUEST_SOURCE") == "app":
        import otp
        state = g.load_json(g.STATE_FILE, {})
        if ts in state.get("create_done", []):   # the same request sent on twice (inbox watcher restart)
            print("This request was already handled: nothing to do.")
            import push
            push.skip()
            return 0
        status.start("create", ts, f"✍️ {'🎬 Reel' if reel_wanted else '📰 Post'}: {raw[:60]}", 6, "Started on GitHub")
        used = state.get("used_otps", [])
        try:
            sent_at = float(ts or "0")
        except ValueError:
            sent_at = 0
        ok, why = otp.verify(os.environ.get("CREATE_TOTP_SECRET", ""), os.environ.get("CREATE_OTP", ""), sent_at, used)
        if not ok:
            print(f"Rejected create request: {why}")
            status.fail({"wrong code": "Wrong Authenticator code. Send it again with the current 6-digit code.",
                         "code already used": "This Authenticator code was already used. Wait for the next code and send again.",
                         "request too old": "The request arrived too late for its code. Send it again.",
                         "code must be 6 digits": "The Authenticator code must be 6 digits. Send it again."}.get(why, why))
            return 0
        state["used_otps"] = (used + [os.environ["CREATE_OTP"].strip() + ":" + str(int(sent_at // 30))])[-200:]
        state["create_done"] = (state.get("create_done", []) + [ts])[-200:]
        g.save_json(g.STATE_FILE, state)
        print("One-time code OK")
        status.step("Code OK: working on it", 2)
    if os.environ.get("MEDIA"):
        media = json.loads(os.environ["MEDIA"])
        if reel_wanted and media.get("video"):
            return make_media_reel(raw, media)
        return make_media(raw, media)
    return make_reel(raw) if reel_wanted else make(raw)


PROMPT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_request": {"type": "boolean", "description": "True if the text asks us to FIND a story (an instruction/topic), "
                                                          "false if it already IS a news story or fact to post"},
        "pick": {"type": "integer", "description": "Number of the best matching candidate (0 if none fits)"},
    },
    "required": ["is_request", "pick"],
    "additionalProperties": False,
}


def resolve_prompt(text: str) -> dict | None:
    """Typed text like "latest viral trending news" is a request: search the feeds (24 h) + Bing News
    and let the AI pick the best story. Returns that story's article, or None if the text is the news."""
    import fun
    saved = g.CONFIG["lookback_hours"], g.CONFIG["per_feed"]
    g.CONFIG["lookback_hours"], g.CONFIG["per_feed"] = 24, 6
    try:
        cands = [{"url": i["link"], "site": i["source"], "title": i["title"], "summary": i["summary"][:160]}
                 for i in g.fetch_candidates(set())]
    finally:
        g.CONFIG["lookback_hours"], g.CONFIG["per_feed"] = saved
    cands += [{**a, "summary": ""} for a in bing_articles(text, limit=10) if a.get("title")]
    if not cands:
        return None
    lines = "\n".join(f"[{n}] ({c['site']}) {c['title']} {c['summary']}" for n, c in enumerate(cands, 1))
    system = (f"You help the owner of {g.CONFIG['page_name']}, an Indian Gen Z news page, create a post. Decide if their "
              f"message is a REQUEST to find a story (e.g. 'latest viral news', 'best cricket story') or is itself the "
              f"news to post. If it's a request, pick the candidate that best fits it and would go most viral with "
              f"young Indians (follow the page's political line: {g.SYSTEM_PROMPT.split('Politics: ')[1].split(chr(10))[0][:250]}).")
    try:
        out = fun.claude_json(system, f"Owner's message: {text}\n\nCandidates:\n{lines}", PROMPT_SCHEMA,
                              g.CONFIG.get("membership_model", "sonnet"))
    except Exception as e:
        print(f"  ! prompt check failed, treating text as the news: {e}")
        return None
    if not out["is_request"] or not 1 <= out["pick"] <= len(cands):
        return None
    chosen = cands[out["pick"] - 1]
    print(f"Prompt '{text[:40]}' -> picked: ({chosen['site']}) {chosen['title']}")
    article = read_article(chosen["url"])
    # Some sites block reading the page: fall back to the headline/summary found in the search.
    if not article["title"] or article["title"].lower() in ("not set", "access denied"):
        article["title"] = chosen["title"]
        article["site"] = chosen["site"]
    if not article["description"]:
        article["description"] = chosen.get("summary") or chosen["title"]
    return article


def make(raw: str, kind: str = "custom") -> int:
    """Build and save a post from a link and/or text (used by ➕ Create and 📅 calendar previews)."""
    urls = re.findall(r"https?://\S+", raw)
    text = re.sub(r"https?://\S+", "", raw).strip()
    article = read_article(urls[0]) if urls else None
    if not urls and text and (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI")):
        picked = resolve_prompt(text)
        if picked:
            article, text = picked, ""      # the request isn't news itself: write from the chosen article
    if article:
        print(f"Link: {article['site']} | {article['title']}")

    summary_file = g.Path(os.environ.get("SUMMARY_FILE", g.ROOT / ".create_summary.md"))
    feed = g.load_json(g.FEED_FILE, [])   # duplicates are allowed: the owner decides

    # Never make a post out of nothing (e.g. a link whose page couldn't be read and no text).
    if not text and not (article and (article.get("title") or article.get("description"))):
        print("No story found to write about: not creating a post.")
        failed("No story found: the link couldn't be read and there was no text. Send a different link or a few words about the news.")
        return 0

    post = None
    note("Writing the post", 3)
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI"):
        try:
            post = fun.normalize({**write_post(text, article), "card_text": ""})
            print("Mode: membership")
        except Exception as e:
            print(f"  ! AI failed, using free mode: {e}")
    if post is None:
        post = free_post(text, article)
        print("Mode: free (no AI)")
    print(f"Headline: {post['headline']}")

    note("Finding photos + making the slides", 4)
    photos = find_photos(article, post)
    banner_source = article["site"] if article else g.CONFIG["page_name"]

    # Carousel slides after the cover (photos already offered as covers aren't repeated).
    slides = []
    item = {"link": article["url"] if article else "", "source": article["site"] if article else "",
            "title": (article or {}).get("title") or post["headline"]}
    import carousel
    import proofread
    specs = []
    try:
        specs = carousel.plan(item, post, [img for img, _ in photos])  # typed news: photo search only
    except Exception as e:
        print(f"  ! carousel failed, single image only: {e}")

    # Proofread the first cover option + the slides; fixes apply to every cover option.
    first_img, first_site = photos[0] if photos else (None, banner_source)

    def render(d):
        card = g.render_card(first_img, d["post"], first_site)
        return [carousel.mark_cover(card)] + carousel.render(d["specs"]) if d["specs"] else [card]

    article_txt = carousel.article_text(carousel.page_html(item["link"])) if item["link"] else text
    note("Proofreading", 5)
    checked, data, proof = proofread.run(render, {"post": post, "specs": specs},
                                         lambda d: caption(d["post"], article["site"] if article else None),
                                         article_txt, drop=proofread.drop_photo_slides)
    post = data["post"]
    slides = checked[1:]
    cards = [g.render_card(img, post, site) for img, site in photos] + [g.render_card(None, post, banner_source)]

    post_id = "c" + g.hashlib.sha1((raw + datetime.now(timezone.utc).isoformat()).encode()).hexdigest()[:11]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    g.POSTS_DIR.mkdir(parents=True, exist_ok=True)
    options = []
    if slides:
        cards = [carousel.mark_cover(c) for c in cards]
    for n, card in enumerate(cards, 1):
        name = f"posts/{stamp}-{post_id}-{n}.jpg"
        card.save(g.DOCS / name, "JPEG", quality=88, optimize=True)
        options.append(name)
    slide_names = []
    for n, slide in enumerate(slides, 2):
        name = f"posts/{stamp}-{post_id}-s{n}.jpg"
        slide.save(g.DOCS / name, "JPEG", quality=86, optimize=True)
        slide_names.append(name)

    state = g.load_json(g.STATE_FILE, {"seen": [], "recent_headlines": []})
    source_url = article["url"] if article else ""
    feed.insert(0, {
        "id": post_id, "kind": kind, "image": options[0], "options": options, "slides": slide_names,
        "headline": post["headline"], "tag": post["tag"],
        "caption": g.limit_hashtags(caption(post, article["site"] if article else None)),
        "source": article["site"] if article else "Your pick", "source_url": source_url,
        "pin_comment": post.get("pin_comment", ""), "proof": proof,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    g.trim_feed(feed)
    state["recent_headlines"] = (state["recent_headlines"] + [f"[{post['tag']}] {post['headline']}"])[-48:]
    g.POSTED += 1
    if source_url:  # the hourly robot won't pick this story up again
        state["seen"] = (state["seen"] + [g.hashlib.sha1(source_url.encode()).hexdigest()[:12]])[-3000:]
    g.save_json(g.FEED_FILE, feed)
    g.save_json(g.STATE_FILE, state)

    summary = (f"✅ **Post ready:** {post['headline']}\n\n"
               f"{len(photos)} cover option(s) + brand banner, and {len(slide_names)} more carousel slide(s). "
               f"Open the KhabarBawaal Studio app (pull to refresh), swipe to pick a cover, then tap 📤 Post.")
    summary_file.write_text(summary)
    ready(post["headline"], post_id)
    print(summary)
    return 0


def frames_from(media: dict) -> list[Image.Image]:
    """Download the frame sheet from the inbox and cut it back into single frames."""
    url = media.get("url", "")
    if not url.startswith("https://ntfy.sh/file/"):
        raise RuntimeError(f"Unexpected media link: {url[:80]}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    sheet = Image.open(io.BytesIO(resp.content)).convert("RGB")
    n, cols = int(media.get("n", 1)), int(media.get("cols", 1))
    rows = -(-n // cols)
    w, h = sheet.width // cols, sheet.height // rows
    return [sheet.crop(((i % cols) * w, (i // cols) * h, (i % cols + 1) * w, (i // cols + 1) * h)) for i in range(n)]


def numbered_sheet(frames: list[Image.Image], cols: int) -> Image.Image:
    """The frames again, with big numbers so the AI can say which one makes the best cover."""
    w, h = frames[0].size
    rows = -(-len(frames) // cols)
    sheet = Image.new("RGB", (cols * w, rows * h), "black")
    d = g.ImageDraw.Draw(sheet)
    for i, f in enumerate(frames):
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(f.resize((w, h)), (x, y))
        d.rectangle((x, y, x + 90, y + 90), fill="black")
        d.text((x + 45, y + 45), str(i + 1), font=g.font("Anton-Regular.ttf", 64), fill="#FFD400", anchor="mm")
    sheet.thumbnail((1800, 1800))
    return sheet


def make_media(text: str, media: dict) -> int:
    """🎬 Post for the owner's own video/photos: cover options, on-video text, caption, pinned comment.
    The owner posts the video from their gallery; only the frames come here."""
    import proofread
    import tempfile
    summary_file = g.Path(os.environ.get("SUMMARY_FILE", g.ROOT / ".create_summary.md"))
    frames = frames_from(media)
    kind_word = "video" if media.get("type") == "video" else "photos"
    print(f"Media: {len(frames)} frame(s) from {kind_word} | context: {text[:80]}")
    note(f"Writing the text for your {kind_word}", 3)

    post = None
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI"):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                numbered_sheet(frames, int(media.get("cols", 1))).save(g.Path(tmp) / "frames.jpg", quality=85)
                system = MEDIA_SYSTEM.format(page=g.CONFIG["page_name"], lang=g.CONFIG["caption_language"],
                                             engage=g.ENGAGE_RULES, n=len(frames))
                user = (f"Owner's context ({kind_word}): {text}\n\n"
                        f"Our recent caption endings (use a different style):\n{g.recent_endings()}\n\n"
                        "Open frames.jpg first, then write the post.")
                post = fun.claude_json(system, user, MEDIA_POST, g.CONFIG.get("membership_model", "sonnet"),
                                       folder=tmp, purpose="video post")
            post = fun.normalize({**post, "card_text": ""})
            print("Mode: membership")
        except Exception as e:
            print(f"  ! AI failed, using free mode: {e}")
    if post is None:
        post = {"tag": "VIRAL", "headline": g.short_headline(text), "highlight": text.split()[:2],
                "overlay_text": g.short_headline(text), "caption": text + "\n\nAap kya sochte ho? 👇",
                "pin_comment": "", "hashtags": ["#viral", "#trending", "#india", "#reels", "#instagood"],
                "cover_frame": 1}
    print(f"Headline: {post['headline']}\nOn video: {post['overlay_text']}")

    # Cover options: the AI's pick, two other moments, then the brand banner.
    best = min(max(int(post.get("cover_frame") or 1), 1), len(frames)) - 1
    others = [i for i in range(len(frames)) if i != best]
    picks = [frames[best]] + [frames[i] for i in others[len(others) // 3::max(1, len(others) // 2)][:2]]
    source = g.CONFIG["page_name"]

    def render(d):
        return [g.render_card(picks[0], d["post"], source)]

    _, data, proof = proofread.run(render, {"post": post}, lambda d: caption(d["post"], None),
                                   f"Owner's context: {text}\nOn-video text: {post['overlay_text']}")
    post = data["post"]
    cards = [g.render_card(img, post, source) for img in picks] + [g.render_card(None, post, source)]

    post_id = "v" + g.hashlib.sha1((text + media.get("url", "") + datetime.now(timezone.utc).isoformat()).encode()).hexdigest()[:11]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    g.POSTS_DIR.mkdir(parents=True, exist_ok=True)
    options = []
    for n, card in enumerate(cards, 1):
        name = f"posts/{stamp}-{post_id}-{n}.jpg"
        card.save(g.DOCS / name, "JPEG", quality=88, optimize=True)
        options.append(name)

    feed = g.load_json(g.FEED_FILE, [])
    feed.insert(0, {
        "id": post_id, "kind": "custom", "media": kind_word, "image": options[0], "options": options, "slides": [],
        "headline": post["headline"], "tag": post["tag"],
        "caption": g.limit_hashtags(caption(post, None)),
        "overlay_text": post["overlay_text"].strip(),
        "source": f"Your {kind_word}", "source_url": "",
        "pin_comment": post.get("pin_comment", ""), "proof": proof,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    g.trim_feed(feed)
    state = g.load_json(g.STATE_FILE, {"seen": [], "recent_headlines": []})
    state["recent_headlines"] = (state["recent_headlines"] + [f"[{post['tag']}] {post['headline']}"])[-48:]
    g.POSTED += 1
    g.save_json(g.FEED_FILE, feed)
    g.save_json(g.STATE_FILE, state)

    summary = (f"✅ **Video post ready:** {post['headline']}\n\nOn-video text: {post['overlay_text']}\n\n"
               f"Open the KhabarBawaal Studio app (pull to refresh) for the caption, comment and {len(options)} cover options.")
    summary_file.write_text(summary)
    ready(post["headline"], post_id)
    print(summary)
    return 0


def has_ai() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI"))


def make_reel(raw: str) -> int:
    """🎬 Reel for a topic/link/news typed in ➕ Create (no video): news -> photo slideshow,
    a funny idea -> 'Tag that friend' clip or meme. Falls back to a normal post if no Reel can be made."""
    import tempfile
    import reel
    summary_file = g.Path(os.environ.get("SUMMARY_FILE", g.ROOT / ".create_summary.md"))
    if not has_ai():
        print("Reels need the AI: making a normal post instead.")
        FALLBACK.append("the AI isn't available")
        return make(raw)
    urls = re.findall(r"https?://\S+", raw)
    text = re.sub(r"https?://\S+", "", raw).strip()
    article = read_article(urls[0]) if urls else None
    if article and not (article.get("title") or article.get("description")):
        article = None
    if not urls and text:
        picked = resolve_prompt(text)   # "latest cricket news" -> a real story
        if picked:
            article, text = picked, ""
    state = g.load_json(g.STATE_FILE, {"seen": [], "recent_headlines": []})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cover, mp4, entry = reel.topic_reel(text, article, state, tmp)
            feed = g.load_json(g.FEED_FILE, [])
            post_id = "c" + g.hashlib.sha1((raw + "|reel" + datetime.now(timezone.utc).isoformat()).encode()).hexdigest()[:11]
            reel.save_reel(feed, state, cover, mp4, post_id, {**entry, "kind": "custom"})
    except Exception as e:
        print(f"  ! Reel not made ({str(e)[:200]}): making a normal post instead.")
        FALLBACK.append(f"Reel not possible: {str(e)[:150]}")
        note("Reel not possible: making a normal post instead", 3)
        code = make(raw)
        if summary_file.exists():
            summary_file.write_text("ℹ️ A Reel couldn't be made for this, so a normal post was made.\n\n"
                                    + summary_file.read_text())
        return code
    summary = (f"✅ **Reel ready:** {entry['headline']}\n\n"
               f"Open the KhabarBawaal Studio app (pull to refresh), add a song from 🎵 Song ideas, then tap 📤 Post.")
    summary_file.write_text(summary)
    print(summary)
    return 0


REEL_RULES = """

This is a REEL: the owner's video with OUR text burned onto it (these rules win):
- overlay_text: the hook shown big at the top of the video for the whole Reel, 4-14 words, Hinglish, \
makes people stop scrolling and watch till the end. NO emojis (the video font can't show them).
- highlight: 1-3 words copied exactly from overlay_text to colour.
- songs: see the list below."""


def make_media_reel(text: str, media: dict) -> int:
    """🎬 Reel from the owner's own video: the AI writes the hook (burned onto the video), caption,
    pinned comment and song ideas from the context + frames; the video's own sound is kept."""
    import proofread
    import tempfile
    import reel
    summary_file = g.Path(os.environ.get("SUMMARY_FILE", g.ROOT / ".create_summary.md"))
    url = media.get("video", "")
    if not url.startswith("https://ntfy.sh/file/") or not has_ai():
        print("No usable video link (or no AI): making the usual video post instead.")
        FALLBACK.append("the video didn't arrive" if not url else "the AI isn't available")
        return make_media(text, media)
    frames = frames_from(media)
    print(f"Own video Reel | context: {text[:80]}")
    note("Downloading your video", 2)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            clip = os.path.join(tmp, "own" + os.path.splitext(urlsplit(url).path)[1].lower())
            open(clip, "wb").write(resp.content)
            dur = min(15.0, reel.video_info(clip)[2] - 0.05)
            if dur < 1:
                raise RuntimeError("video too short or unreadable")
            note("Writing the hook, caption + comment", 3)
            songs = reel.trending_songs()
            numbered_sheet(frames, int(media.get("cols", 1))).save(g.Path(tmp) / "frames.jpg", quality=85)
            schema = {**MEDIA_POST, "properties": {**MEDIA_POST["properties"], "songs": reel.SONGS},
                      "required": MEDIA_POST["required"] + ["songs"]}
            system = MEDIA_SYSTEM.format(page=g.CONFIG["page_name"], lang=g.CONFIG["caption_language"],
                                         engage=g.ENGAGE_RULES, n=len(frames)) + REEL_RULES
            post = fun.claude_json(system, f"Owner's context (video): {text}\n\n"
                                   f"Our recent caption endings (use a different style):\n{g.recent_endings()}\n\n"
                                   "Open frames.jpg first, then write the post." + reel.songs_prompt(songs),
                                   schema, g.CONFIG.get("membership_model", "sonnet"), folder=tmp, purpose="video reel")
            post = fun.normalize({**post, "card_text": ""})
            reel.roman_only(post, ["caption", "pin_comment"])
            picked_songs = reel.checked_songs(post.pop("songs", []), songs)
            out = os.path.join(tmp, "reel.mp4")
            note("Making the video + proofreading", 5)
            checks, data, proof = proofread.run(
                lambda d: reel.own_reel(clip, dur, d["post"]["overlay_text"], d["post"]["highlight"], out),
                {"post": post}, lambda d: caption(d["post"], None), f"Owner's context: {text}")
            post = data["post"]
            feed = g.load_json(g.FEED_FILE, [])
            state = g.load_json(g.STATE_FILE, {"seen": [], "recent_headlines": []})
            state["recent_headlines"] = (state["recent_headlines"] + [f"[{post['tag']}] {post['headline']}"])[-48:]
            post_id = "v" + g.hashlib.sha1((text + url + datetime.now(timezone.utc).isoformat()).encode()).hexdigest()[:11]
            reel.save_reel(feed, state, checks[0], out, post_id, {
                "kind": "custom", "headline": post["overlay_text"].strip(), "tag": "🎬 REEL · " + post["tag"],
                "caption": g.limit_hashtags(caption(post, None)), "source": "Your video", "source_url": "",
                "pin_comment": post.get("pin_comment", ""), "songs": picked_songs, "proof": proof})
        except Exception as e:
            print(f"  ! own-video Reel failed ({str(e)[:200]}): making the usual video post instead.")
            FALLBACK.append(f"Reel not possible: {str(e)[:150]}")
            note("Reel not possible: making the usual video post instead", 3)
            return make_media(text, media)
    summary = (f"✅ **Reel ready from your video:** {post['overlay_text']}\n\n"
               f"Open the KhabarBawaal Studio app (pull to refresh), add a song from 🎵 Song ideas, then tap 📤 Post.")
    summary_file.write_text(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    import limits
    fun.CURRENT_JOB = "create"
    try:
        code = main()
    except Exception as e:
        failed(f"Something went wrong: {str(e)[:200]}")
        raise
    finally:
        fun.flush_usage(posts=g.POSTED)
        limits.record()
    sys.exit(code)
