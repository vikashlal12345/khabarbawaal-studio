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


def topic(link: dict) -> str:
    tag = re.sub(r"[^\w &]", "", link.get("tag", "")).replace("SAMPLE", "").strip().upper()
    return tag or link.get("kind", "news").upper()


def learn(state: dict) -> None:
    stats = g.DOCS / "stats"
    links = json.loads((stats / "ig_links.json").read_text()) if (stats / "ig_links.json").exists() else {}
    insta = json.loads((stats / "instagram.json").read_text()) if (stats / "instagram.json").exists() else {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=21)
    rows = []
    for pid, link in links.items():
        res = insta.get("posts", {}).get(pid)
        if not res or not link.get("linked_at") or datetime.fromisoformat(link["linked_at"]) < cutoff:
            continue
        rows.append((topic(link), link.get("kind", "news"), res["likes"] + 2 * res["comments"]))
    if len(rows) < MIN_POSTS:
        print(f"Learning: {len(rows)}/{MIN_POSTS} linked posts with results, not enough yet.")
        state["audience"] = {"summary": "", "posts": len(rows)}
        return
    avg = lambda key: sorted(((k, sum(e for t, kd, e in rows if (t if key == 0 else kd) == k) /
                               sum(1 for t, kd, e in rows if (t if key == 0 else kd) == k))
                              for k in {r[key] for r in rows}), key=lambda kv: -kv[1])
    topics, kinds = avg(0), avg(1)
    best = ", ".join(f"{k} ({v:.0f})" for k, v in topics[:3])
    worst = ", ".join(f"{k} ({v:.0f})" for k, v in topics[-2:]) if len(topics) > 3 else ""
    summary = (f"From our last {len(rows)} Instagram posts (engagement = likes + 2×comments): best topics {best}"
               + (f"; weakest {worst}" if worst else "") + ". Formats: "
               + ", ".join(f"{k} {v:.0f}" for k, v in kinds) + ". Lean toward what works, keep some variety.")
    state["audience"] = {"summary": summary, "posts": len(rows),
                         "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (stats / "audience.json").write_text(json.dumps({**state["audience"], "topics": topics, "kinds": kinds}, indent=1))
    print("Learning:", summary)
