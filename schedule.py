"""When to post (IST). No third-party imports, so the workflow can use it before installing anything.

    python schedule.py next-wait   -> seconds until the next posting slot
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

IST = timedelta(hours=5, minutes=30)
QUIET_START, QUIET_END = 23, 5                       # no posts 11 PM - 5 AM
# All Reels (owner, 13 Oct 2026: carousels weren't getting views; Reels reach people who don't follow us).
# 15 Oct 2026 (owner): 13 Reels a day (incl. the 4 PM poll), all in the feed; every news slot is the single most viral / trending story
# (the Top 5s became viral news, +11:30 AM and 3 PM); night Reels (1 AM Ready, 3 AM God picture) and the 2 AM
# preview planner stopped. No regular carousels (news, Top 5s, Thought, markets, night jobs still exist as --kind).
REGULAR_HOURS: list[int] = []
# 9 Reels (owner, 19 Oct 2026: poll + one news + one breaking card removed, no replacement).
SPECIALS = {"reel_devotion": (6, 0),                       # 🙏 festival / Navratri goddess / god of the day
            "reel_news_early": (8, 0),                     # 🔥 viral news
            "reel_break_am": (10, 0),                      # ⚡ breaking card
            "reel_news_am": (12, 0),                       # 🔥 viral news
            "reel_break_noon": (14, 0),                    # ⚡ breaking card
            "reel_news_pm": (16, 0),                       # 🔥 viral news
            "reel_news_aft": (18, 0),                      # 🔥 viral news (owner, 29 Oct 2026: replaces the 🎨 cartoon roast; Breaking or Viral only after 6 PM)
            "reel_break_aft": (19, 45),                    # ⚡ breaking card (owner, 23 Oct 2026: replaces the 🎭 rotating joke slot, it did not go well)
            "reel_news_night": (20, 15),                   # 🔥 viral news (owner, 29 Oct 2026: replaces the 🎥🔊 speaker video until the page has 1,000+ followers)
            "night_learn": (4, 0)}                         # night work, nothing posted: learning from stats
PREVIEW_KINDS: tuple[str, ...] = ()                  # 📅 planned previews stopped (every news slot = most viral story)
MANUAL = {"top10_viral", "top10_day", "night_roundup", "thought", "market_open", "market_preopen", "market_close",
          "reel_top5_am", "reel_top5", "night_ready", "night_calendar", "night_picture",
          "reel_news_noon", "reel_clip_am", "reel_clip", "reel_meme", "reel_poll", "reel_news",
          "reel_rotate", "reel_bachpan", "reel_flash", "reel_politics", "reel_official"}
NIGHT_JOBS = {"night_ready", "night_calendar", "night_picture", "night_learn"}   # 3 AM: 🙏 god picture (bhakti.py)   # joke bank retired: jokes are trend-only
SPECIAL_WINDOW = (-3, 55)                             # minutes around a special's time that count as its run (19 Oct 2026: GitHub started the 9:30 PM run 25 min late and the slot was lost)
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


def preview_times() -> list[tuple[int, int]]:
    """Slots where a 📅 planned preview can go out (as that slot's news Reel)."""
    return [SPECIALS[k] for k in PREVIEW_KINDS]


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
