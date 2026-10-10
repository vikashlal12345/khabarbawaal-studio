"""When to post (IST). No third-party imports, so the workflow can use it before installing anything.

    python schedule.py next-wait   -> seconds until the next posting slot
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

IST = timedelta(hours=5, minutes=30)
QUIET_START, QUIET_END = 23, 5                       # no posts 11 PM - 5 AM
# Fewer, stronger posts (owner's plan, 10 Oct 2026): ~9 a day = 5 Reels + 4 carousels.
# Regular posts (news carousels with "KhabarBawaal ka take") at these hours; specials below.
REGULAR_HOURS = [10, 17]
# (Top 5 Viral, market posts and Thought of the Day still exist as --kind options, just not scheduled.)
SPECIALS = {"reel_devotion": (6, 0),                       # 🎬 🙏 festival / Navratri goddess / god of the day
            "night_roundup": (7, 0),                       # 🌅 overnight Top 5 carousel
            "reel_clip_am": (8, 0), "reel_clip": (13, 0),  # 🎬 Tag that friend
            "reel_politics": (18, 0),                      # 🎬 political satire + reality check
            "reel_meme": (19, 0), "reel_news": (20, 0),    # 🎬 meme, news slideshow
            "top10_day": (21, 15),                         # 📰 Top 5 News carousel
            # Night work (nothing is posted): ready posts, day planning, learning from stats.
            "night_ready": (1, 0), "night_calendar": (2, 0), "night_learn": (4, 0)}
NIGHT_JOBS = {"night_ready", "night_calendar", "night_learn"}   # joke bank retired: jokes are trend-only
SPECIAL_WINDOW = (-3, 12)                             # minutes around a special's time that count as its run
WINDOWS = {"market_preopen": (-1, 5)}                 # pre-open data exists only after 9:08


def ist_now() -> datetime:
    return datetime.now(timezone.utc) + IST


def is_quiet(t: datetime) -> bool:
    return t.hour >= QUIET_START or t.hour < QUIET_END


def special_due(t: datetime, done: dict) -> str | None:
    """The special post whose slot is now (the closest one), unless already made today."""
    due = []
    for kind, (h, m) in SPECIALS.items():
        slot = t.replace(hour=h, minute=m, second=0, microsecond=0)
        mins = (t - slot).total_seconds() / 60
        lo, hi = WINDOWS.get(kind, SPECIAL_WINDOW)
        if lo <= mins <= hi and done.get(kind) != t.date().isoformat():
            due.append((abs(mins), kind))
    return min(due)[1] if due else None


def regular_times() -> list[tuple[int, int]]:
    """hh:00 in REGULAR_HOURS (5 AM - 10 PM)."""
    return [(h, 0) for h in REGULAR_HOURS]


def slots(day: datetime) -> list[datetime]:
    base = day.replace(hour=0, minute=0, second=0, microsecond=0)
    out = [base.replace(hour=h, minute=m) for h, m in regular_times()]
    out += [base.replace(hour=h, minute=m) for h, m in SPECIALS.values()]
    return sorted(out)


def next_wait(now: datetime | None = None, min_gap: int = 120) -> int:
    now = now or ist_now()
    upcoming = [s for d in (now, now + timedelta(days=1)) for s in slots(d)
                if (s - now).total_seconds() >= min_gap]
    return int((upcoming[0] - now).total_seconds())


if __name__ == "__main__":
    if sys.argv[1:] == ["next-wait"]:
        print(next_wait())
    else:
        print(__doc__)
