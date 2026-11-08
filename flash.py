"""⚡ Flash watch + 🔁 repeat winners (owner's choice, 29 Oct 2026: 'do 1, 2, 3').

    python flash.py [--dry-run]

Runs every 30 min from .github/workflows/flash-watch.yml (no AI, only the free news feeds + our own Instagram numbers).

⚡ FLASH: when a big film star is in the news of 6+ different sites in the last 4 h (4+ when the name also trends on X; Bing News counts) and our
   Reels have not covered that star in the last 6 h, that story is PINNED to the next news / breaking slot (owner, 29 Oct 2026: 'always put it in
   the next slot, the gap is just 2 hours': no extra Reel at a random time). A slot less than 30 min away is too close; if the next slot is
   more than 3.5 h away (overnight) the story is left alone.
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
# stories about religious conflict are never pinned automatically (30 Oct 2026: 'Sara Ali Khan faces fatwa over Kedarnath Temple visit' came up):
# the page must never fan hate between communities, so a human decides on those
# deaths and mourning are never pinned automatically either (1 Nov 2026: 'Hrithik mourns Kaabil director Sanjay Gupta' was pinned to the 8 AM slot)
ROUTINE = re.compile(r"(?i)\b(birthday|b'day|bday|anniversary|wishes|wishing|greets|greetings|happy diwali|happy holi|festive wishes|celebrates diwali|celebrates halloween)\b")
PROMO = re.compile(r"(?i)\b(trailer|teaser|poster|first look|first-look|box office|advance booking|release date|motion poster|song launch|promo|early impressions)\b")
DEATH = re.compile(r"(?i)\b(died|dies|dead|death|passes away|passed away|passing|demise|funeral|last rites|cremation|mourns|mourning|mourn|"
                   r"tribute to (the )?late|late (friend|actor|director|singer)|condolences?|rip|shraddhanjali|final journey)\b")
SENSITIVE = re.compile(r"(?i)fatwa|blasphem|communal|riot|jihad|conversion|namaz|mosque|masjid|azaan|\bbeef\b|lynch|boycott|hate speech|"
                       r"religious sentiments|hurt(ing)? sentiments|offend(s|ed|ing)? (hindu|muslim|christian|sikh)|anti-hindu|anti-muslim")
AMBIGUOUS = {"king", "yash", "vijay", "shah", "srk"}        # words that are not always a star
ALIASES = {"srk": "shah rukh", "shahrukh": "shah rukh", "shah ruk": "shah rukh"}
PEAK_SLOTS = {"reel_news_am", "reel_news_pm"}   # 12 PM and 4 PM: the best hours (avg 1,808 / 1,829 views since 20 Oct 2026): pins go there first when they fit
SERIES = ("koffee with karan", "ramayana")       # reliable series: Koffee With Karan 2,715 avg, Ramayana 1,519 avg
NEWS_SLOTS = ("reel_news_early", "reel_break_am", "reel_news_am", "reel_break_noon", "reel_news_pm", "reel_news_aft", "reel_break_aft", "reel_news_night")


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
    import create_post
    cutoff = time.time() - 8 * 3600
    trend_text = " ".join(t for t, _ in trends).lower()
    # star names in today's feed headlines and in X trends (the feeds alone hold too few headlines to show a pile-up)
    names: dict[str, int] = {}
    for it in cands:
        if it["ts"] >= cutoff and not emergency.SKIP_WORDS.search(it["title"]) and not SENSITIVE.search(it["title"]) and not DEATH.search(it["title"]) and not ROUTINE.search(it["title"]):
            for raw in set(m.lower() for m in emergency.STARS.findall(it["title"])):
                names[norm_name(raw)] = names.get(norm_name(raw), 0) + 1
    for raw in set(m.lower() for m in emergency.STARS.findall(" ".join(t for t, _ in trends[:80]))):
        names[norm_name(raw)] = names.get(norm_name(raw), 0) + 1
    for term in SERIES:   # 📺 series watch: a fresh Koffee With Karan / Ramayana story counts as a flash story with a lower bar (3 sites)
        names.setdefault(term, 0)
        names[term] += 5
    best = None
    now = datetime.now(timezone.utc)
    pinned = list((state.get("pinned") or {}).values())
    for name in sorted((n for n in names if n not in AMBIGUOUS), key=lambda n: -names[n])[:8]:
        if covered_recently(name) or any(p.get("flash") == name or name in p.get("title", "").lower() for p in pinned):
            continue   # already made, or already waiting in a slot (flash-watch and the post chain both run this check)
        sites, arts = set(), []
        for a in create_post.bing_articles(f"{name} bollywood", limit=15, newest=True):
            d = bing_dt(a)
            if d is None:
                continue
            if (now - d).total_seconds() <= 4 * 3600 and name.split()[-1] in a["title"].lower() and not emergency.SKIP_WORDS.search(a["title"]) \
                    and not SENSITIVE.search(a["title"]) and not DEATH.search(a["title"]) and not PROMO.search(a["title"]) and not ROUTINE.search(a["title"]):
                sites.add(a["site"])
                arts.append(a)
        need = 3 if name in SERIES else 4 if name in trend_text else 6
        print(f"  flash check: {name}: {len(sites)} sites in 4 h (need {need})")
        if len(sites) >= need:
            score = len(sites) + (3 if name in trend_text else 0)
            if not best or score > best[0]:
                best = (score, name, sites, arts)
    if not best:
        return print("  flash: no mega story right now") or None
    score, name, sites, arts = best
    import reel
    fresh = [a for a in arts if not reel.covered_before(a["title"], 12)]   # an article about something we already told is no new story
    if not fresh:
        return print(f"  flash: every {name} article is a story we told in the last 12 h") or None
    top = fresh[0]
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
            if d > posted + timedelta(hours=1) and (now - d).total_seconds() < 36 * 3600 and a["url"] not in used and not SENSITIVE.search(a["title"]) and not DEATH.search(a["title"]) \
                    and all(a["url"] != p.get("link") for p in (state.get("pinned") or {}).values()):
                fresh.append(a)
        sites = {a["site"] for a in fresh}
        state.setdefault("followed_winners", []).append(m["id"])   # asked once, whatever the answer
        state["followed_winners"] = state["followed_winners"][-80:]
        if len(sites) >= 2:
            print(f"  🔁 winner '{query}' ({m['views']} views): {len(sites)} sites report a new development")
            return {"link": fresh[0]["url"], "title": fresh[0]["title"], "source": fresh[0]["site"], "winner": query}
        print(f"  winners: '{query}' has no new development yet")
    return None


def next_slot(state: dict, now_ist: datetime, lead_min: int = 30, max_wait_h: float = 24) -> str | None:
    """The next news / breaking slot at least `lead_min` minutes away that has no pin yet (and not more than `max_wait_h` hours away)."""
    pins = state.get("pinned") or {}
    options = []
    for kind in NEWS_SLOTS:
        h, m = schedule.SPECIALS[kind]
        slot = now_ist.replace(hour=h, minute=m, second=0, microsecond=0)
        if slot < now_ist + timedelta(minutes=lead_min):
            slot += timedelta(days=1)
        if kind not in pins and (slot - now_ist).total_seconds() <= max_wait_h * 3600:
            options.append((slot, kind))
    if not options:
        return None
    peak = [o for o in options if o[1] in PEAK_SLOTS]
    return min(peak)[1] if peak else min(options)[1]


def hot_now(state: dict, cands: list[dict]) -> list[dict]:
    """🔥 What is hot RIGHT NOW, computed at creation time by the picker itself (owner, 1 Nov 2026: 'do not pin a story so early; check the story
    at creation time only, so we always have the latest trending one'): a star / series story that 6+ sites run in the last 4 h (the Flash check),
    and, once a day, a fresh development on our own hit (the winner check). Returned as story dicts marked `hot`; the picker lists them first,
    after its own filters (already told, avoided). Never writes a pin."""
    import fun
    now_ist = schedule.ist_now()
    out = []
    try:
        fl = detect_flash(state, now_ist, cands, fun.x_trends() or [])
        if fl:
            out.append({"id": "", "source": fl["source"], "category": "entertainment", "title": fl["title"], "summary": "", "link": fl["link"],
                        "image": "", "ts": time.time(), "hot": f"{fl['name']}: many sites right now"})
    except Exception as e:
        print(f"  ! hot check failed: {type(e).__name__}: {str(e)[:100]}")
    try:
        win = detect_winner(state, now_ist)
        if win:
            state["winner_followup_day"] = now_ist.date().isoformat()
            out.append({"id": "", "source": win["source"], "category": "entertainment", "title": win["title"], "summary": "", "link": win["link"],
                        "image": "", "ts": time.time(), "hot": "a new development on our own hit"})
    except Exception as e:
        print(f"  ! follow-up check failed: {type(e).__name__}: {str(e)[:100]}")
    return out


def main() -> int:
    """No pinning any more (1 Nov 2026): the picker calls hot_now() when it makes a Reel. `python flash.py` only prints what is hot now."""
    state = g.load_json(g.STATE_FILE, {}) or {}
    print(f"hot now ({schedule.ist_now():%a %H:%M} IST):")
    for h in hot_now(state, g.fetch_candidates(set())):
        print(f"  🔥 {h['hot']}: {h['title'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
