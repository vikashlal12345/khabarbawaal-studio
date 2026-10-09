"""📦 Ready posts: evergreen carousels made at night (facts, explainers, on-this-day,
quizzes, myth vs fact) plus 2 timeless 🎬 Reels (a "Tag that friend" clip and a meme). They wait in docs/ready.json (max 15, kept 7 days) for the owner
to post any time, and are used automatically when the AI can't write a news post.
"""
from __future__ import annotations

import json
import random
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from PIL import Image

import fun
import generate as g

READY_FILE = g.DOCS / "ready.json"
MAX_READY, KEEP_DAYS = 15, 7
FORMATS = {
    "facts": "5 amazing, little-known facts about one topic Indians love (India, cricket, Bollywood, space, food, history, tech)",
    "explainer": "a simple explainer of something people hear about but don't fully understand (e.g. GIFT Nifty, repo rate, ISRO missions, UPI)",
    "on_this_day": "what happened on this date in history (India or world) that young Indians would find interesting",
    "quiz": "a 4-question quiz; the answers go on the last text slide so people swipe and comment their score",
    "myth_fact": "4 common myths Indians believe, each busted with the real fact",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "tag": {"type": "string", "description": "1-2 words, e.g. AMAZING FACTS, EXPLAINER, ON THIS DAY, QUIZ, MYTH VS FACT"},
        "headline": {"type": "string", "description": "Cover title, max 10 words, English, catchy"},
        "highlight": {"type": "array", "items": {"type": "string"}},
        "slides": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Max 30 words, Hinglish in English letters"},
            "wiki_title": {"type": "string", "description": "English Wikipedia title for this slide's photo, or empty"}},
            "required": ["text", "wiki_title"], "additionalProperties": False},
            "description": "4-5 slides"},
        "cover_wiki_title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tag", "headline", "highlight", "slides", "cover_wiki_title", "caption", "hashtags"],
    "additionalProperties": False,
}

SYSTEM = """You make evergreen Instagram carousels for {page}, an Indian page for 18-35 year olds. \
They must stay interesting for weeks (no dated news). Hinglish in English letters only, never \
Devanagari. Only well-established facts you are sure of: no invented numbers, quotes or dates; \
if unsure, choose a different fact. Clean, family-safe, no politics. Each slide's wiki_title \
names a real English Wikipedia article whose main photo fits that slide (or empty). Caption: \
2-3 Hinglish lines ending with a question or "save karo / share karo". Hashtags: exactly 5, the most popular high-reach hashtags relevant to the post (mix big ones like #viral #trending #india #bollywood #cricket with 1-2 specific ones), no brand tag."""


def load() -> list:
    return json.loads(READY_FILE.read_text()) if READY_FILE.exists() else []


def save(items: list) -> None:
    READY_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=1))


def prune(items: list) -> list:
    """Drop expired posts (and their images); keep the newest MAX_READY."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date().isoformat()
    keep = [p for p in items if datetime.fromisoformat(p["created_at"]) >= cutoff
            and p.get("valid_on", today) == today][-MAX_READY:]   # "on this day" posts only on their date
    ids = {p["id"] for p in keep}
    for p in items:
        if p["id"] not in ids:
            for img in g.post_images(p):
                (g.DOCS / img).unlink(missing_ok=True)
    return keep


def photo(title: str) -> Image.Image | None:
    import create_post
    return g.download_image(create_post.wikipedia_photo(title)) if title else None


def make_one(fmt: str, recent: list[str]) -> dict | None:
    import carousel
    import proofread

    try:
        out = fun.claude_json(SYSTEM.format(page=g.CONFIG["page_name"]),
                              f"{fun.today_context()}\nFormat: {FORMATS[fmt]}.\nAvoid these recent topics:\n"
                              + ("\n".join(f"- {r}" for r in recent) or "(none)"),
                              SCHEMA, g.CONFIG.get("membership_model", "sonnet"))
    except Exception as e:
        print(f"  ! ready post failed: {e}")
        return None
    cover_img = photo(out["cover_wiki_title"])
    specs = []
    for sl in out["slides"][:5]:
        img = photo(sl["wiki_title"])
        specs.append({"type": "photo", "img": img, "credit": "Wikimedia Commons", "text": sl["text"]} if img
                     else {"type": "text", "title": out["tag"], "text": sl["text"]})
    specs.append({"type": "closing"})
    post = {"tag": out["tag"], "headline": out["headline"], "highlight": out["highlight"],
            "caption": out["caption"], "hashtags": out["hashtags"]}

    def render(d):
        card = carousel.mark_cover(g.render_card(cover_img, d["post"], "Wikimedia Commons" if cover_img else ""))
        return [card] + carousel.render(d["specs"])

    def caption(d):
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in d["post"]["hashtags"])
        return f"{d['post']['caption'].strip()}\n\nFollow {g.CONFIG['handle']} for daily updates.\n\n{tags}"

    slides, data, proof = proofread.run(render, {"post": post, "specs": specs}, caption,
                                        drop=proofread.drop_photo_slides)
    return {"slides": slides, "caption": caption(data), "proof": proof, "headline": data["post"]["headline"],
            "tag": "📦 " + data["post"]["tag"].upper()}


def make_reel(kind: str, state: dict) -> dict | None:
    """A timeless joke Reel for the 📦 Ready tab (video + cover saved under docs/posts)."""
    import reel
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cover, mp4, entry = reel.build_joke_reel(kind, state, tmp, evergreen=True)
            pid = f"r{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{kind}"
            cover.save(g.DOCS / f"posts/{pid}.jpg", "JPEG", quality=88, optimize=True)
            shutil.copy(mp4, g.DOCS / f"posts/{pid}.mp4")
    except Exception as e:
        print(f"  ! ready Reel failed: {str(e)[:200]}")
        return None
    print(f"  ready Reel: {entry['headline']}")
    return {"id": pid, "kind": "ready", "media": "reel", "image": f"posts/{pid}.jpg", "video": f"posts/{pid}.mp4",
            **entry, "tag": "📦 " + entry["tag"], "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def make_batch(state: dict, count: int = 5) -> int:
    """count posts: up to ready_reels_per_night Reels, the rest carousels (one more carousel per failed Reel)."""
    items = prune(load())
    recent = state.get("recent_ready", [])[-40:]
    made = 0
    for kind in ["reel_clip", "reel_meme"][:min(count, g.CONFIG.get("ready_reels_per_night", 2))]:
        out = make_reel(kind, state)
        if out:
            items.append(out)
            made += 1
    for fmt in random.sample(list(FORMATS), k=min(count - made, len(FORMATS))):
        out = make_one(fmt, recent)
        if not out:
            continue
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        pid = f"r{stamp}-{fmt}"
        names = []
        for n, im in enumerate(out["slides"], 1):
            name = f"posts/{pid}-{n}.jpg"
            im.save(g.DOCS / name, "JPEG", quality=86, optimize=True)
            names.append(name)
        items.append({"id": pid, "kind": "ready", "image": names[0], "slides": names[1:],
                      "headline": out["headline"], "tag": out["tag"], "caption": g.limit_hashtags(out["caption"]),
                      "source": "KhabarBawaal Original", "source_url": "", "proof": out["proof"],
                      "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      **({"valid_on": (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date().isoformat()}
                         if fmt == "on_this_day" else {})})
        recent.append(out["headline"])
        made += 1
        print(f"  ready post: {out['headline']}")
    state["recent_ready"] = recent[-60:]
    save(prune(items))
    return made


def take_one(feed: list, state: dict) -> bool:
    """Move the oldest ready post into the feed (used when the AI can't write a news post)."""
    items = prune(load())
    if not items:
        return False
    # Today's "on this day" post first (it expires tonight), else the oldest.
    today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date().isoformat()
    idx = next((i for i, p in enumerate(items) if p.get("valid_on") == today), 0)
    post = items.pop(idx)
    post["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    post["proof"] = {**(post.get("proof") or {}), "issues": (post.get("proof") or {}).get("issues", []) +
                     ["Used from 📦 Ready posts because the AI couldn't write a news post this time."]}
    feed.insert(0, post)
    save(items)
    g.trim_feed(feed)
    g.save_json(g.FEED_FILE, feed)
    g.save_json(g.STATE_FILE, state)
    print(f"Used ready post: {post['headline']}")
    return True
