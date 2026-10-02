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
        "photo_query": {"type": "string", "description": "Short English news-search query to find photos of this story"},
        "wiki_title": {"type": "string", "description": "English Wikipedia article title of the main person, team or place, or empty"},
    },
    "required": ["tag", "headline", "highlight", "caption", "hashtags", "photo_query", "wiki_title"],
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
        out.append({"url": real, "site": html.unescape(src.group(1)).split(" on ")[0] if src else urlsplit(real).netloc})
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


def caption(post: dict, source: str | None) -> str:
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in post["hashtags"])
    src = f"Source: {source} (link pinned in comments 👇)\n" if source else ""
    return f"{post['caption'].strip()}\n\nFollow {g.CONFIG['handle']} for daily updates.\n{src}\n{tags}"


def main() -> int:
    raw = os.environ.get("INPUT_TEXT", "").strip()
    if not raw:
        print("No input given.")
        return 1
    urls = re.findall(r"https?://\S+", raw)
    text = re.sub(r"https?://\S+", "", raw).strip()
    article = read_article(urls[0]) if urls else None
    if article:
        print(f"Link: {article['site']} | {article['title']}")

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
    cards = [g.render_card(img, post, site) for img, site in photos] + [g.render_card(None, post, banner_source)]

    post_id = "c" + g.hashlib.sha1(raw.encode()).hexdigest()[:11]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    g.POSTS_DIR.mkdir(parents=True, exist_ok=True)
    options = []
    for n, card in enumerate(cards, 1):
        name = f"posts/{stamp}-{post_id}-{n}.jpg"
        card.save(g.DOCS / name, "JPEG", quality=88, optimize=True)
        options.append(name)

    feed = g.load_json(g.FEED_FILE, [])
    state = g.load_json(g.STATE_FILE, {"seen": [], "recent_headlines": []})
    source_url = article["url"] if article else ""
    feed.insert(0, {
        "id": post_id, "kind": "custom", "image": options[0], "options": options,
        "headline": post["headline"], "tag": post["tag"],
        "caption": caption(post, article["site"] if article else None),
        "source": article["site"] if article else "Your pick", "source_url": source_url,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    for old in feed[g.CONFIG["max_posts_kept"]:]:
        for img in old.get("options", [old["image"]]):
            (g.DOCS / img).unlink(missing_ok=True)
    del feed[g.CONFIG["max_posts_kept"]:]
    state["recent_headlines"] = (state["recent_headlines"] + [f"[{post['tag']}] {post['headline']}"])[-48:]
    g.save_json(g.FEED_FILE, feed)
    g.save_json(g.STATE_FILE, state)

    summary = (f"✅ **Post ready:** {post['headline']}\n\n"
               f"{len(photos)} photo option(s) + brand banner. Open the KhabarBawaal Studio app "
               f"(pull to refresh), swipe to pick a version, then tap 📤 Post.")
    g.Path(os.environ.get("SUMMARY_FILE", g.ROOT / ".create_summary.md")).write_text(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
