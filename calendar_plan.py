"""📅 Day planner: at night, find today's big scheduled events (matches, releases, results,
festivals, big political events) in the news, and plan up to 3 preview posts for the right
time. At that slot the preview is made like a ➕ Create post (photos, carousel, proofread).
"""
from __future__ import annotations

import os

import fun
import generate as g
import schedule

SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": {"type": "object", "properties": {
        "time": {"type": "string", "description": "One of the allowed HH:MM slots, a few hours before the event"},
        "topic": {"type": "string", "description": "Short English, e.g. 'India vs Australia 2nd ODI preview'"},
        "brief": {"type": "string", "description": "2-3 sentences of facts from the headlines: what, when, where, why it matters"},
        "search": {"type": "string", "description": "English news-search query to find articles and photos"}},
        "required": ["time", "topic", "brief", "search"], "additionalProperties": False},
        "description": "0-3 events"}},
    "required": ["events"], "additionalProperties": False,
}


def plan(state: dict) -> int:
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI")):
        return 0
    today = schedule.ist_now()
    saved = g.CONFIG["lookback_hours"], g.CONFIG["per_feed"]
    g.CONFIG["lookback_hours"], g.CONFIG["per_feed"] = 36, 8
    try:
        items = g.fetch_candidates(set())
    finally:
        g.CONFIG["lookback_hours"], g.CONFIG["per_feed"] = saved
    slots = [f"{h:02d}:{m:02d}" for h, m in schedule.regular_times()
             if h >= 7 and (h, m) not in schedule.SPECIALS.values()]   # not the 🎬 Reel hours
    lines = [f"- ({i['category']}) {i['title']}: {i['summary'][:160]}" for i in items[:70]]
    system = (f"You plan preview posts for {g.CONFIG['page_name']}, an Indian Instagram news page for Gen Z. "
              f"From the headlines, find events SCHEDULED FOR TODAY ({today:%A %d %B %Y}) that young Indians care about: "
              f"cricket or other big matches, movie/OTT releases, exam results, big launches, festivals, major "
              f"political events (follow the page's political line: {g.SYSTEM_PROMPT.split('Politics: ')[1].split(chr(10))[0][:300]}). "
              f"Only events the headlines clearly say happen today. Pick at most 3, the biggest. Time each preview a "
              f"few hours before the event, using only these slots: {', '.join(slots)}. If nothing big is scheduled, "
              f"return no events.")
    try:
        out = fun.claude_json(system, "Headlines:\n" + "\n".join(lines), SCHEMA,
                              g.CONFIG.get("membership_model", "sonnet"))
    except Exception as e:
        print(f"  ! calendar planning failed: {e}")
        return 0
    events = [e for e in out["events"] if e["time"] in slots][:3]
    state["calendar"] = {"date": today.date().isoformat(), "items": [{**e, "done": False} for e in events]}
    for e in events:
        print(f"  planned {e['time']}: {e['topic']}")
    return len(events)


def due(state: dict) -> dict | None:
    cal = state.get("calendar") or {}
    now = schedule.ist_now()
    if cal.get("date") != now.date().isoformat():
        return None
    for e in cal.get("items", []):
        h, m = map(int, e["time"].split(":"))
        mins = (now - now.replace(hour=h, minute=m, second=0, microsecond=0)).total_seconds() / 60
        if not e["done"] and -5 <= mins <= 20:
            return e
    return None


def make_preview(event: dict) -> bool:
    """Make the preview post like a ➕ Create post; True if a post was saved."""
    import create_post
    fun.CURRENT_JOB = "calendar"
    articles = create_post.bing_articles(event["search"], limit=3)
    raw = f"{event['topic']}. {event['brief']} " + (articles[0]["url"] if articles else "")
    before = g.POSTED
    create_post.make(raw, kind="calendar")
    return g.POSTED > before
