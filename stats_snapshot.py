"""📊 Facts for the stats page that need no Instagram access (owner, 22 Oct 2026: 'without recordings, plan something').

Every robot run (cheap, never breaks a run):
- docs/stats/trends.json: what India is talking about now (X trends from trends24.in, Google trends, the biggest stories of
  the feeds) so the stats page can show which of them our Reels already covered and which not;
- docs/stats/made.json: a ledger of the posts the robot made, per day (kept 30 days; the feed itself keeps 24 h), so the
  page can compare 'made' with the owner's ✅ posted marks.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import generate as g

STATS = g.DOCS / "stats"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def trends(max_age_min: int = 40) -> None:
    import fun
    path = STATS / "trends.json"
    old = _read(path, {})
    if old.get("at") and datetime.now(timezone.utc) - datetime.fromisoformat(old["at"]) < timedelta(minutes=max_age_min):
        return
    try:
        x = fun.x_trends()
    except Exception:
        x = []
    try:
        google = fun.google_trends()
    except Exception:
        google = []
    x = [(t, h) for t, h in x if not re.search(r"[\u0900-\u097F\u0980-\u0DFF]", t)]   # Roman letters only (like our Reels)
    hours = lambda t: max((h for term, h in x if len(term) >= 4 and term.lower().lstrip("#") in t.lower()), default=0)
    stories = []
    india = []
    try:
        items = g.fetch_candidates(set())
        india = [(t, h) for t, h in x if any(t.lower().lstrip("#") in i["title"].lower() for i in items if len(t) >= 4)]
        items.sort(key=lambda i: -hours(i["title"]))
        for i in items[:40]:
            if i.get("category", "") in ("politics", "crime", "disaster", "death"):
                continue
            stories.append({"title": re.sub(r"\s+", " ", i["title"]).strip()[:140], "source": i.get("source", ""),
                            "link": i["link"], "category": i.get("category", ""), "hours": hours(i["title"])})
            if len(stories) >= 10:
                break
    except Exception as e:
        print(f"  stats trends: stories failed ({str(e)[:80]})")
    if not (x or google or stories):
        return
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    known = {k: v for k, v in (old.get("known") or {}).items() if v >= cutoff}   # every story link shown on the stats page in 24 h
    known.update({st["link"]: now_iso for st in stories})   # (📌 pins are accepted only for these: the robot's own list)
    STATS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": now_iso, "known": known,
                                "x": [{"term": t, "hours": h} for t, h in (india if len(india) >= 3 else x)[:15]],   # trends that also appear in India news headlines
                                "google": [t for t in google if not re.search(r"[\u0900-\u097F\u0980-\u0DFF]", t)][:10], "stories": stories},
                               ensure_ascii=False, indent=1))
    print(f"  stats trends: {len(x)} X, {len(google)} Google, {len(stories)} stories")


def made() -> None:
    feed = g.load_json(g.FEED_FILE, [])
    path = STATS / "made.json"
    ledger = _read(path, {})
    for p in feed:
        try:
            day = (datetime.fromisoformat(p["created_at"]) + timedelta(hours=5, minutes=30)).date().isoformat()
        except Exception:
            continue
        ledger.setdefault(day, {})[p["id"]] = {"tag": p.get("tag", ""), "at": p["created_at"],
                                               "auto": "AUTO MODE" in p.get("tag", "")}
    keep = sorted(ledger)[-30:]
    STATS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({d: ledger[d] for d in keep}, ensure_ascii=False, indent=1))


def run() -> None:
    for fn in (made, trends):
        try:
            fn()
        except Exception as e:
            print(f"  stats snapshot {fn.__name__} failed: {str(e)[:100]}")


if __name__ == "__main__":
    run()
