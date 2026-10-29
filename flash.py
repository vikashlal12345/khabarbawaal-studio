"""⚡ Flash watch + 🔁 repeat winners (owner's choice, 29 Oct 2026: 'do 1, 2, 3').

    python flash.py [--dry-run]

Runs every 30 min from .github/workflows/flash-watch.yml (no AI, only the free news feeds + our own Instagram numbers).

⚡ FLASH: when a big film star is in the news of 6+ different sites in the last 4 h (4+ when the name also trends on X; Bing News counts) and our
   Reels have not covered that star in the last 6 h, one extra breaking-card Reel (kind `reel_flash`) is made right away with that story.
   Limits: 2 a day, 3 h apart, never 11 PM-6 AM, never within 40 min of a scheduled slot. The workflow dispatches the hourly run.
🔁 WINNERS: a Reel 6-36 h old with a big number (views 2.5x the median and 3K+, or 5+ shares, or 5+ saves) gets a follow-up: a fresh
   development on the same story (2+ sites, newer than the Reel) is pinned to the next news / breaking slot, once a day.
Writes state/history.json (pinned, flash_log, followed_winners); prints `FLASH=1` for the workflow when a flash run is needed."""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import generate as g
import schedule

ROOT = Path(__file__).parent
AMBIGUOUS = {"king", "yash", "vijay", "shah", "srk"}        # words that are not always a star
ALIASES = {"srk": "shah rukh", "shahrukh": "shah rukh", "shah ruk": "shah rukh"}
NEWS_SLOTS = ("reel_news_early", "reel_break_am", "reel_news_am", "reel_break_noon", "reel_news_pm", "reel_break_aft")


def norm_name(n: str) -> str:
    n = re.sub(r"\s+", " ", n.lower().strip())
    return ALIASES.get(n.replace(" ", ""), ALIASES.get(n, n))


def bing_dt(a: dict):
    """Bing News RSS writes its times in US Pacific wall-clock but labels them GMT (29 Oct 2026: Mid-Day 'updated 12:57 PM IST' came as
    00:27 GMT = 7 h too early). Read the wall-clock as Pacific time and convert to UTC. None when the date is missing."""
    from zoneinfo import ZoneInfo
    try:
        d = datetime.fromisoformat(a.get("date", ""))
    except ValueError:
        return None
    return d.replace(tzinfo=None).replace(tzinfo=ZoneInfo("America/Los_Angeles")).astimezone(timezone.utc)


def near_slot(now_ist: datetime, minutes: int = 40) -> bool:
    for _, (h, m) in schedule.SPECIALS.items():
        slot = now_ist.replace(hour=h, minute=m, second=0, microsecond=0)
        if abs((now_ist - slot).total_seconds()) < minutes * 60:
            return True
    return False


def covered_recently(name: str, hours: int = 6) -> bool:
    """Did one of our Reels (docs/feed.json) already name this star in the last `hours`?"""
    now = datetime.now(timezone.utc)
    for p in g.load_json(g.DOCS / "feed.json", []) or []:
        try:
            age = (now - datetime.fromisoformat(p["created_at"])).total_seconds() / 3600
        except Exception:
            continue
        if age <= hours and name in (p.get("headline", "") + " " + p.get("caption", "")[:200]).lower():
            return True
    return False


def detect_flash(state: dict, now_ist: datetime, cands: list[dict], trends: list) -> dict | None:
    import emergency
    log = [datetime.fromisoformat(x) for x in state.get("flash_log", [])]
    today = [t for t in log if (t + schedule.IST).date() == now_ist.date()]
    if len(today) >= 2:
        return print("  flash: 2 already today") or None
    if log and (datetime.now(timezone.utc) - max(log)).total_seconds() < 3 * 3600:
        return print("  flash: last one less than 3 h ago") or None
    if schedule.is_quiet(now_ist) or now_ist.hour < 6:
        return print("  flash: quiet hours") or None
    if near_slot(now_ist):
        return print("  flash: a scheduled slot is close") or None
    import create_post
    cutoff = time.time() - 8 * 3600
    trend_text = " ".join(t for t, _ in trends).lower()
    # star names in today's feed headlines and in X trends (the feeds alone hold too few headlines to show a pile-up)
    names: dict[str, int] = {}
    for it in cands:
        if it["ts"] >= cutoff and not emergency.SKIP_WORDS.search(it["title"]):
            for raw in set(m.lower() for m in emergency.STARS.findall(it["title"])):
                names[norm_name(raw)] = names.get(norm_name(raw), 0) + 1
    for raw in set(m.lower() for m in emergency.STARS.findall(" ".join(t for t, _ in trends[:80]))):
        names[norm_name(raw)] = names.get(norm_name(raw), 0) + 1
    best = None
    now = datetime.now(timezone.utc)
    for name in sorted((n for n in names if n not in AMBIGUOUS), key=lambda n: -names[n])[:8]:
        if covered_recently(name):
            continue
        sites, arts = set(), []
        for a in create_post.bing_articles(f"{name} bollywood", limit=15, newest=True):
            d = bing_dt(a)
            if d is None:
                continue
            if (now - d).total_seconds() <= 4 * 3600 and name.split()[-1] in a["title"].lower() and not emergency.SKIP_WORDS.search(a["title"]):
                sites.add(a["site"])
                arts.append(a)
        need = 4 if name in trend_text else 6
        print(f"  flash check: {name}: {len(sites)} sites in 4 h (need {need})")
        if len(sites) >= need:
            score = len(sites) + (3 if name in trend_text else 0)
            if not best or score > best[0]:
                best = (score, name, sites, arts)
    if not best:
        return print("  flash: no mega story right now") or None
    score, name, sites, arts = best
    top = arts[0]
    print(f"  ⚡ FLASH: {name} in {len(sites)} sites: {top['title'][:90]}")
    return {"link": top["url"], "title": top["title"], "source": top["site"], "name": name}


def detect_winner(state: dict, now_ist: datetime) -> dict | None:
    api = g.load_json(g.DOCS / "stats" / "instagram_api.json", {}) or {}
    reels = [m for m in api.get("media", []) if m.get("views") is not None]
    if len(reels) < 12:
        return None
    today = now_ist.date().isoformat()
    if state.get("winner_followup_day") == today:
        return print("  winners: already one follow-up today") or None
    med = statistics.median(m["views"] for m in reels)
    now = datetime.now(timezone.utc)
    followed = set(state.get("followed_winners", []))
    cands = []
    for m in reels:
        try:
            age = (now - datetime.strptime(m["time"], "%Y-%m-%dT%H:%M:%S%z")).total_seconds() / 3600
        except Exception:
            continue
        if not 6 <= age <= 36 or m["id"] in followed:
            continue
        if m["views"] >= max(3000, 2.5 * med) or (m.get("shares") or 0) >= 5 or (m.get("saves") or 0) >= 5:
            cands.append((m["views"] + 100 * (m.get("shares") or 0) + 60 * (m.get("saves") or 0), m, age))
    if not cands:
        return print("  winners: no Reel is a winner right now") or None
    import create_post
    used = set(state.get("reel_news_used", []))
    for _, m, age in sorted(cands, key=lambda c: -c[0]):
        query = re.sub(r"#\w+|[^\w\s'-]", " ", (m.get("caption") or "").split("\n")[0])
        query = re.sub(r"\s+", " ", query).strip()[:90]
        if len(query) < 8:
            continue
        posted = datetime.strptime(m["time"], "%Y-%m-%dT%H:%M:%S%z")
        fresh = []
        for a in create_post.bing_articles(query, limit=10, newest=True):
            d = bing_dt(a)
            if d is None:
                continue
            if d > posted + timedelta(hours=1) and (now - d).total_seconds() < 36 * 3600 and a["url"] not in used:
                fresh.append(a)
        sites = {a["site"] for a in fresh}
        state.setdefault("followed_winners", []).append(m["id"])   # asked once, whatever the answer
        state["followed_winners"] = state["followed_winners"][-80:]
        if len(sites) >= 2:
            print(f"  🔁 winner '{query}' ({m['views']} views): {len(sites)} sites report a new development")
            return {"link": fresh[0]["url"], "title": fresh[0]["title"], "source": fresh[0]["site"], "winner": query}
        print(f"  winners: '{query}' has no new development yet")
    return None


def next_slot(state: dict, now_ist: datetime) -> str | None:
    """The next news / breaking slot at least 75 min away (today, else tomorrow's first) that has no pin yet."""
    pins = state.get("pinned") or {}
    options = []
    for kind in NEWS_SLOTS:
        h, m = schedule.SPECIALS[kind]
        slot = now_ist.replace(hour=h, minute=m, second=0, microsecond=0)
        if slot < now_ist + timedelta(minutes=75):
            slot += timedelta(days=1)
        if kind not in pins:
            options.append((slot, kind))
    return min(options)[1] if options else None


def main() -> int:
    dry = "--dry-run" in sys.argv
    import fun
    state = g.load_json(g.STATE_FILE, {}) or {}
    now_ist = schedule.ist_now()
    print(f"flash watch {now_ist:%a %H:%M} IST" + (" (dry run)" if dry else ""))
    flash = None
    try:
        cands = g.fetch_candidates(set())
        flash = detect_flash(state, now_ist, cands, fun.x_trends() or [])
    except Exception as e:
        print(f"  ! flash check failed: {type(e).__name__}: {str(e)[:120]}")
    changed = False
    if flash:
        state.setdefault("pinned", {})["reel_flash"] = {"link": flash["link"], "title": flash["title"][:200], "source": flash["source"],
                                                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        state.setdefault("flash_log", []).append(datetime.now(timezone.utc).isoformat(timespec="seconds"))
        state["flash_log"] = state["flash_log"][-20:]
        changed = True
    try:
        win = detect_winner(state, now_ist)
        changed = True   # followed_winners may have grown
        if win:
            slot = next_slot(state, now_ist)
            if slot:
                state.setdefault("pinned", {})[slot] = {"link": win["link"], "title": win["title"][:200], "source": win["source"],
                                                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "followup": win["winner"]}
                state["winner_followup_day"] = now_ist.date().isoformat()
                print(f"  🔁 pinned to {slot}: {win['title'][:80]}")
    except Exception as e:
        print(f"  ! winners check failed: {type(e).__name__}: {str(e)[:120]}")
    if changed and not dry:
        g.save_json(g.STATE_FILE, state)
    print("FLASH=1" if flash else "FLASH=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
