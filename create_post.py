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

CUSTOM_POST = {
    "type": "object",
    "properties": {
        "tag": {"type": "string", "description": "1-2 word label, e.g. BREAKING, VIRAL, POLITICS, CRICKET, BOLLYWOOD, TECH"},
        "headline": {"type": "string"},
        "highlight": {"type": "array", "items": {"type": "string"},
                      "description": "1-3 words copied exactly from the headline to colour"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "key_facts": {"type": "array", "items": {"type": "string"},
                      "description": "3 short Hinglish facts from the story, max 14 words each"},
        "photo_query": {"type": "string", "description": "Short English news-search query to find photos of this story"},
        "wiki_title": {"type": "string", "description": "English Wikipedia article title of the main person, team or place, or empty"},
    },
    "required": ["tag", "headline", "highlight", "caption", "hashtags", "key_facts", "photo_query", "wiki_title"],
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


def write_post(text: str, article: dict | None) -> dict:
    parts = []
    if article:
        parts.append(f"Article ({article['site']}): {article['title']}\n{article['description']}")
    if text:
        parts.append(f"Owner's note: {text}")
    user = "\n\n".join(parts)
    system = CUSTOM_SYSTEM.format(page=g.CONFIG["page_name"], lang=g.CONFIG["caption_language"])
    return fun.claude_json(system, user, CUSTOM_POST, g.CONFIG.get("membership_model", "sonnet"))


def free_post(text: str, article: dict | None) -> dict:
    title = (article or {}).get("title") or text
    return {"tag": "BREAKING", "headline": g.short_headline(title), "highlight": title.split()[:2],
            "caption": ((article or {}).get("description") or text) + "\n\nAap kya sochte ho? 👇 Comment karo!",
            "hashtags": ["#India", "#News", "#Trending", "#KhabarBawaal"],
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
    if not raw:
        print("No input given.")
        return 1
    # Requests from the app's inbox (not a GitHub issue) must carry the owner's one-time code.
    if os.environ.get("REQUEST_SOURCE") == "app":
        import otp
        state = g.load_json(g.STATE_FILE, {})
        used = state.get("used_otps", [])
        try:
            sent_at = float(os.environ.get("CREATE_TS", "0"))
        except ValueError:
            sent_at = 0
        ok, why = otp.verify(os.environ.get("CREATE_TOTP_SECRET", ""), os.environ.get("CREATE_OTP", ""), sent_at, used)
        if not ok:
            print(f"Rejected create request: {why}")
            return 0
        state["used_otps"] = (used + [os.environ["CREATE_OTP"].strip() + ":" + str(int(sent_at // 30))])[-200:]
        g.save_json(g.STATE_FILE, state)
        print("One-time code OK")
    return make(raw)


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
    feed = g.load_json(g.FEED_FILE, [])
    force = re.search(r"\bagain\b", text, re.I)
    if not force:
        dup = find_duplicate(feed, article["url"] if article else "",
                             [article["title"] if article else "", text])
        if dup:
            msg = (f"⚠️ **Already posted** on {ist_time(dup['created_at'])} IST:\n\n> {dup['headline']}\n\n"
                   f"Not creating it again. To post it anyway, send it again with the word **again** in your message.")
            summary_file.write_text(msg)
            print(msg)
            return 0

    # Never make a post out of nothing (e.g. a link whose page couldn't be read and no text).
    if not text and not (article and (article.get("title") or article.get("description"))):
        print("No story found to write about: not creating a post.")
        return 0

    post = None
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
    checked, data, proof = proofread.run(render, {"post": post, "specs": specs},
                                         lambda d: caption(d["post"], article["site"] if article else None),
                                         article_txt, drop=proofread.drop_photo_slides)
    post = data["post"]
    slides = checked[1:]
    cards = [g.render_card(img, post, site) for img, site in photos] + [g.render_card(None, post, banner_source)]

    post_id = "c" + g.hashlib.sha1(raw.encode()).hexdigest()[:11]
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
        "caption": caption(post, article["site"] if article else None),
        "source": article["site"] if article else "Your pick", "source_url": source_url, "proof": proof,
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
    print(summary)
    return 0


if __name__ == "__main__":
    import limits
    fun.CURRENT_JOB = "create"
    try:
        code = main()
    finally:
        fun.flush_usage(posts=g.POSTED)
        limits.record()
    sys.exit(code)
