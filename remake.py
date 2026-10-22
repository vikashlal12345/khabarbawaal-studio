"""🔄 Remake a Reel you did not like (owner, 22 Oct 2026): the app's 🔄 button sends the post id + what you did not like (and the
authenticator code, like ➕ Create). The robot makes the Reel again with your comment in front of every writing / picking
prompt, keeping the same story / speaker video / god / format (unless you ask for another story), and replaces the old one.

    remake.run(post_id, comment) -> int       (called by create_post.main when REMAKE is set)
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone

import generate as g
import status


def _drop(feed: list, post: dict) -> None:
    """Removes an old post from the feed and deletes its files."""
    if post in feed:
        feed.remove(post)
    names = [post.get("image", ""), post.get("video", "")] + list(post.get("slides", []))
    for n in names:
        if not n:
            continue
        for path in (g.DOCS / n, g.DOCS / n.replace("posts/", "posts/p/", 1)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _same_story(comment: str, old: dict) -> bool:
    """True = keep the story / video (the default); False when the owner asks for another story."""
    import fun
    schema = {"type": "object", "properties": {"new_story": {"type": "boolean", "description": "True only if the owner asks "
              "for a DIFFERENT story / topic / video; false if he wants the same one fixed"}}, "required": ["new_story"],
              "additionalProperties": False}
    try:
        res = fun.claude_json("You read the page owner's comment on a Reel he did not like.", f"Reel: {old.get('headline', '')}\n"
                              f"His comment: {comment}", schema, g.CONFIG.get("membership_model", "sonnet"), purpose="remake intent")
        return not res["new_story"]
    except Exception as e:
        if type(e).__name__ == "ClaudeLimit":
            raise
        return True


def run(post_id: str, comment: str, check_posted: bool = True) -> int:
    import fun
    import reel
    feed = g.load_json(g.FEED_FILE, [])
    state = g.load_json(g.STATE_FILE, {})
    olds = sorted([p for p in feed if p["id"] == post_id], key=lambda p: p["created_at"])
    if not olds:
        status.fail("Remake: this Reel is no longer in the app (older than 24 hours?)")
        return 0
    old = olds[-1]
    links = g.load_json(g.DOCS / "stats" / "ig_links.json", {})
    if check_posted and post_id in links:
        status.fail("Remake: this Reel is already marked as posted, so it was not changed")
        return 0
    if not old.get("video"):
        status.fail("Remake works for Reels only")
        return 0
    m = re.match(r"^(reel_[a-z0-9_]+)-(\d{4}-\d{2}-\d{2})$", post_id)
    kind, today = (m.group(1), m.group(2)) if m else ("custom", "")
    same = _same_story(comment, old) if kind != "custom" else True
    fun.OWNER_FEEDBACK = (
        "THE OWNER IS REMAKING THIS REEL because he did not like it. The previous version (only to understand his complaint: "
        "it may even contain a claim the article does not support, so NEVER copy a claim from it; every fact must come from the "
        f"story text): headline '{old.get('headline', '')[:200]}', caption '{old.get('caption', '')[:500]}'.\n"
        f"HIS COMMENT (do exactly this; change only what he complained about and keep everything else that was fine; real facts "
        f"only, never invent anything): {comment}")
    if old.get("source_url") and kind in reel.NEWS_KINDS + reel.BREAK_KINDS and same:
        reel.REMAKE.update({"link": old["source_url"], "title": old.get("headline", ""), "source": old.get("source", "")})
    if kind == "reel_official" and old.get("source_url") and same:
        mm = re.search(r"/status/(\d+)", old["source_url"])
        if mm:
            reel.REMAKE["video_id"] = mm.group(1)
    if kind == "reel_politics" and same and state.get("satire_recent"):
        state["satire_recent"] = state["satire_recent"][:-1]   # the same story may be picked again
    reel.SERIES_KIND = kind
    before = {id(p) for p in feed}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if kind in reel.NEWS_KINDS:
                reel.make_news_reel(kind, state, feed, today, tmp)
            elif kind in reel.BREAK_KINDS:
                reel.make_breaking_reel(kind, state, feed, today, tmp)
            elif kind == "reel_politics":
                reel.make_politics_reel(state, feed, today, tmp)
            elif kind == "reel_rotate":
                reel.make_rotate_reel(state, feed, today, tmp)
            elif kind == "reel_devotion":
                reel.make_devotion_reel(state, feed, today, tmp)
            elif kind == "reel_official":
                reel.make_official_reel(state, feed, today, tmp)
            elif kind == "custom":
                import create_post
                raw = old.get("request") or f"{old.get('source_url', '')} {old.get('headline', '')}".strip()   # older ones: their story link
                known = {p["id"] for p in feed}
                create_post.make_reel(raw)
                feed = g.load_json(g.FEED_FILE, [])
                new = [p for p in feed if p["id"] not in known]
                if not new or not new[0].get("video"):
                    for p in new:
                        _drop(feed, p)
                    g.save_json(g.FEED_FILE, feed)
                    raise RuntimeError("a Reel could not be made again (a normal post was the only result)")
                new[0]["id"] = post_id   # takes the old one's place
                g.save_json(g.FEED_FILE, feed)
            else:
                raise RuntimeError(f"this kind of Reel ({kind}) cannot be remade")
    except Exception as e:
        print(f"  ! remake failed: {type(e).__name__}: {str(e)[:200]}")
        status.fail(f"Remake not possible: {str(e)[:160]}. The old Reel was kept.")
        return 0
    finally:
        fun.OWNER_FEEDBACK = ""
        reel.REMAKE.clear()
    feed = g.load_json(g.FEED_FILE, [])
    same_id = sorted([p for p in feed if p["id"] == post_id], key=lambda p: p["created_at"])
    for p in same_id[:-1]:   # keep the newest, drop the old one and its files
        _drop(feed, p)
    g.save_json(g.FEED_FILE, feed)
    status.done(f"🔄 Remade: {same_id[-1].get('headline', '')[:80]}", post=post_id)
    return 0
