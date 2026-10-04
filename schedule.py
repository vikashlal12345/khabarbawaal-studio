"""When to post (IST). No third-party imports, so the workflow can use it before installing anything.

    python schedule.py next-wait   -> seconds until the next posting slot
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

IST = timedelta(hours=5, minutes=30)
QUIET_START, QUIET_END = 23, 5                       # no posts 11 PM - 5 AM
PEAK_HOURS = [(7, 11), (18, 23)]                      # Instagram's busiest hours: a post every 30 min
SPECIALS = {"top10_viral": (6, 15), "market_open": (8, 15), "market_preopen": (9, 9), "thought": (9, 15),
            "market_close": (15, 45), "top10_day": (21, 15)}   # market posts skip non-trading days
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
    """hh:00 every hour from 5 AM; also hh:30 in peak hours. Last post 10:30 PM."""
    out = []
    for h in range(QUIET_END, QUIET_START):
        out.append((h, 0))
        if any(a <= h < b for a, b in PEAK_HOURS):
            out.append((h, 30))
    return out


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
