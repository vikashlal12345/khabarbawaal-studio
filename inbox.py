"""Inbox for things sent from the app without GitHub (🔗 Insta links).

The app posts small messages to a private ntfy.sh channel (secret random name in config.json).
ntfy keeps messages 12 hours; the robot collects them at the start of every run (every
30-60 minutes), so nothing is missed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import requests

import generate as g


def process() -> int:
    topic = g.CONFIG.get("inbox_topic")
    if not topic:
        return 0
    state = g.load_json(g.STATE_FILE, {})
    since = state.get("inbox_since") or "12h"
    try:
        lines = requests.get(f"https://ntfy.sh/{topic}/json", params={"poll": "1", "since": since}, timeout=20).text
    except requests.RequestException as e:
        print(f"  inbox: {e}")
        return 0
    path = g.DOCS / "stats" / "ig_links.json"
    links = json.loads(path.read_text()) if path.exists() else {}
    feed = g.load_json(g.FEED_FILE, [])
    ready = g.load_json(g.DOCS / "ready.json", [])
    done = 0
    for line in lines.splitlines():
        try:
            m = json.loads(line)
            state["inbox_since"] = m["id"]
            msg = json.loads(m.get("message", "{}"))
        except (json.JSONDecodeError, KeyError):
            continue
        url = re.search(r"https://(?:www\.)?instagram\.com/(?:p|reel)/[A-Za-z0-9_-]+", msg.get("url", ""))
        if msg.get("type") != "insta" or not url or not msg.get("post"):
            continue
        post = next((p for p in feed + ready if p["id"] == msg["post"]), {})
        links[msg["post"]] = {"url": url.group(0), "kind": post.get("kind", "news"), "tag": post.get("tag", ""),
                              "headline": post.get("headline", ""), "posted_at": post.get("created_at", ""),
                              "linked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        done += 1
    if done:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(links, ensure_ascii=False, indent=1))
        print(f"  inbox: saved {done} Instagram link(s)")
    g.save_json(g.STATE_FILE, state)
    return done
