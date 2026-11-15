"""📈 4 AM learning: which topics and formats get the most likes and comments on Instagram.

Uses the posts you linked from the app (🔗 Insta link) and the counts the Mac job collected.
The result is a short note the AI reads when choosing stories ("audience loves cricket and
viral, less interested in tech"), so the page leans toward what works.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import generate as g

MIN_POSTS = 10
MIN_LIKES = 30        # learn only from real signal: at 0-1 likes per post every topic looks the same
OWN_COMMENTS = 1      # you pin one comment (the source link) on every post: don't count it


def topic(link: dict) -> str:
    tag = re.sub(r"[^\w &]", "", link.get("tag", "")).replace("SAMPLE", "").strip().upper()
    return tag or link.get("kind", "news").upper()


def learn(state: dict) -> None:
    stats = g.DOCS / "stats"
    links = json.loads((stats / "ig_links.json").read_text()) if (stats / "ig_links.json").exists() else {}
    insta = json.loads((stats / "instagram.json").read_text()) if (stats / "instagram.json").exists() else {}
    boosts = json.loads((stats / "boosts.json").read_text()) if (stats / "boosts.json").exists() else {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=21)
    rows = []
    for pid, link in links.items():
        if pid in boosts:      # paid reach would mislead the learning
            continue
        res = insta.get("posts", {}).get(pid)
        if not res or not link.get("linked_at") or datetime.fromisoformat(link["linked_at"]) < cutoff:
            continue
        rows.append((topic(link), link.get("kind", "news"),
                     res["likes"] + 2 * max(0, res["comments"] - OWN_COMMENTS), res["likes"]))
    total_likes = sum(r[3] for r in rows)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if len(rows) < MIN_POSTS or total_likes < MIN_LIKES:
        why = (f"{len(rows)}/{MIN_POSTS} linked posts with results" if len(rows) < MIN_POSTS
               else f"only {total_likes}/{MIN_LIKES} likes so far")
        print(f"Learning: not adjusting yet ({why}).")
        state["audience"] = {"summary": "", "posts": len(rows), "likes": total_likes, "updated": now, "waiting": why}
        (stats / "audience.json").write_text(json.dumps(state["audience"], indent=1))
        return
    avg = lambda key: sorted(((k, sum(e for t, kd, e, _ in rows if (t if key == 0 else kd) == k) /
                               sum(1 for t, kd, e, _ in rows if (t if key == 0 else kd) == k))
                              for k in {r[key] for r in rows}), key=lambda kv: -kv[1])
    topics, kinds = avg(0), avg(1)
    best = ", ".join(f"{k} ({v:.0f})" for k, v in topics[:3])
    worst = ", ".join(f"{k} ({v:.0f})" for k, v in topics[-2:]) if len(topics) > 3 else ""
    summary = (f"From our last {len(rows)} Instagram posts (engagement = likes + 2×comments, not counting our own "
               f"pinned comment): best topics {best}"
               + (f"; weakest {worst}" if worst else "") + ". Formats: "
               + ", ".join(f"{k} {v:.0f}" for k, v in kinds) + ". Lean toward what works, keep some variety.")
    state["audience"] = {"summary": summary, "posts": len(rows), "likes": total_likes, "updated": now}
    (stats / "audience.json").write_text(json.dumps({**state["audience"], "topics": topics, "kinds": kinds}, indent=1))
    print("Learning:", summary)
