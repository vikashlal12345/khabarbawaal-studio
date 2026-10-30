"""💡 Weekly suggestions (owner's choice, 29 Oct 2026): once a week Claude reads the Instagram numbers (docs/stats/instagram_api.json) and
proposes 3-5 changes in plain words. They go to docs/stats/suggestions.json; the Stats page shows them with ✅ Approve / ❌ Skip buttons
(the choice comes back through the inbox, inbox.py type 'suggestion'); a phone alert says they are ready. Nothing changes by itself: the
owner opens a chat with Claude, who reads the approved ones and makes them.

    python weekly_suggestions.py
"""
import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
API = ROOT / "docs" / "stats" / "instagram_api.json"
OUT = ROOT / "docs" / "stats" / "suggestions.json"

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string", "description": "2 short plain-language lines: how the week went"},
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string", "description": "Max 10 words, plain language"},
        "why": {"type": "string", "description": "1-2 short sentences using the real numbers from the data"},
        "change": {"type": "string", "description": "One concrete sentence: exactly what Claude would change (a slot, a rule, a picker, a timing)"}},
        "required": ["title", "why", "change"], "additionalProperties": False}}},
    "required": ["summary", "items"], "additionalProperties": False}

SYSTEM = ("You are the analyst of the Instagram page @khabarbawaal (Hinglish viral news and Bollywood Reels for Indian 18-34s, a robot makes 9 Reels a day, "
          "the owner posts by hand and is not technical). Read the real numbers and write 3 to 5 suggestions, best first. Rules: plain simple English, no "
          "jargon; every suggestion rests on numbers in the data; each is something Claude Code can actually change in the robot (a slot's Reel type, "
          "which stories are favoured, hook or cover rules, timing, formats); NEVER suggest removing or changing the 6 AM devotional Reel (the owner "
          "wants to start his day with it; you may suggest small improvements to it); politics must stay pro-BJP/NDA, no hate, no invented facts, no "
          "buy/sell tips; do not repeat a suggestion the owner already skipped or approved before (listed below); do not suggest buying anything. The owner paused the 8:15 PM speaker video and the 6 PM cartoon roast until the page has 1,000+ followers: when the follower count reaches 1,000, make one of the suggestions \"Bring back the speaker video (8:15 PM) as planned\"; before that, never suggest politics or speaker Reels.")


def hour_stats(reels):
    by = {}
    for m in reels:
        t = datetime.strptime(m["time"], "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone(timedelta(hours=5, minutes=30)))
        by.setdefault(t.hour, []).append(m["views"])
    return {h: (round(statistics.mean(v)), len(v)) for h, v in by.items() if len(v) >= 2}


def main() -> int:
    import fun
    try:
        d = json.loads(API.read_text())
    except Exception:
        print("no Instagram numbers yet: nothing to analyse")
        return 0
    now = datetime.now(timezone.utc)
    old = lambda m, h: (now - datetime.strptime(m["time"], "%Y-%m-%dT%H:%M:%S%z")).total_seconds() > h * 3600
    reels = [m for m in d.get("media", []) if m.get("views") is not None and old(m, 24)]
    if len(reels) < 12:
        print("fewer than 12 settled Reels: not enough data")
        return 0
    prev = {}
    try:
        prev = json.loads(OUT.read_text())
    except Exception:
        pass
    line = lambda m: (f"{m['views']} views, {m.get('reach')} reach, {m.get('likes') or 0} likes, {m.get('comments') or 0} comments, {m.get('shares') or 0} shares, "
                      f"{m.get('saves') or 0} saves, {m.get('reposts') or 0} reposts, {(m.get('ig_reels_avg_watch_time') or 0) / 1000:.1f}s watched, posted {m['time'][:16]} UTC: "
                      f"{re.sub(chr(10), ' ', (m.get('caption') or '')[:90])}")
    week = sorted((m for m in reels if not old(m, 24 * 7)), key=lambda m: -m["views"])
    all_best = sorted(reels, key=lambda m: -m["views"])[:8]
    weak = sorted(reels, key=lambda m: m["views"])[:8]
    hours = hour_stats(reels)
    plan = json.loads((ROOT / "docs" / "app.json").read_text()).get("plan", [])
    decided = [f"- [{i['status']}] {i['title']}" for w in [prev] + prev.get("history", []) for i in w.get("items", []) if i.get("status") in ("approved", "skipped")]
    followers_prev = prev.get("followers")
    # followers per day (the hourly snapshots in docs/stats/instagram.json; Meta gives no per-Reel follows number for Reels)
    try:
        snaps = json.loads((ROOT / "docs" / "stats" / "instagram.json").read_text()).get("followers", [])
        ist = timezone(timedelta(hours=5, minutes=30))
        by_day = {}
        for f in snaps:
            by_day[datetime.fromisoformat(f["at"]).astimezone(ist).date().isoformat()] = f["followers"]
        days = sorted(by_day)[-10:]
        follower_line = ("Followers at the end of each day (IST): " + ", ".join(f"{d[5:]}: {by_day[d]}" for d in days)) if len(days) >= 2 else ""
    except Exception:
        follower_line = ""
    user = (f"Followers now: {d['account']['followers']} (a week ago: {followers_prev if followers_prev is not None else 'unknown'}).\n"
            + (follower_line + "\n" if follower_line else "") +
            f"Settled Reels (24 h+ old): {len(reels)}; average views {round(statistics.mean(m['views'] for m in reels))}, median {round(statistics.median(m['views'] for m in reels))}.\n"
            f"Daily plan (IST): " + "; ".join(f"{t} {n}" for t, _, n in plan) + "\n\n"
            "Average views by the hour the Reel was posted (IST), hour: (avg views, Reels): " + json.dumps({str(h): v for h, v in sorted(hours.items())}) + "\n\n"
            f"This week's Reels ({len(week)}), most viewed first:\n" + "\n".join(line(m) for m in week[:14]) + "\n\n"
            "Best 8 of all time:\n" + "\n".join(line(m) for m in all_best) + "\n\nWeakest 8:\n" + "\n".join(line(m) for m in weak) + "\n\n"
            "Earlier suggestions the owner decided on (never repeat these):\n" + ("\n".join(decided) or "(none yet)"))
    try:
        res = fun.claude_json(SYSTEM, user, SCHEMA, "sonnet", purpose="weekly suggestions")
    except Exception as e:
        print(f"! suggestions skipped this week ({type(e).__name__}: {str(e)[:120]})")
        return 0
    iso = now.isocalendar()
    week_id = f"{iso[0]}-W{iso[1]:02d}"
    items = [{"id": f"{now:%Y%m%d}-{n}", "title": i["title"][:120], "why": i["why"][:400], "change": i["change"][:300], "status": "new"}
             for n, i in enumerate(res["items"][:5], 1)]
    history = ([{k: prev[k] for k in ("week", "created", "items") if k in prev}] if prev.get("items") else []) + prev.get("history", [])
    out = {"week": week_id, "created": now.isoformat(timespec="seconds"), "summary": res["summary"][:400], "followers": d["account"]["followers"],
           "items": items, "history": history[:8]}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"saved {len(items)} suggestions for {week_id}")
    try:
        import push
        push.send("💡 Weekly suggestions are ready", f"{len(items)} ideas to approve or skip on the Stats page")
    except Exception as e:
        print(f"  alert not sent: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
