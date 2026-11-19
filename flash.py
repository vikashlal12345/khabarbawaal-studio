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
ROUTINE = re.compile(r"(?i)\b(birthday|b'day|bday|anniversary|wishes|wishing|greets|greetings|happy diwali|happy holi|festive wishes|celebrates diwali|celebrates halloween|men'?s day|women'?s day|children'?s day|friendship day|mother'?s day|father'?s day|valentine'?s day)\b")
PROMO = re.compile(r"(?i)\b(trailer|teaser|poster|first look|first-look|box office|advance booking|release date|motion poster|song launch|promo|early impressions|ticket prices?|tickets? (?:price|booking|sales)|slashed|discount)\b")
DEATH = re.compile(r"(?i)\b(died|dies|dead|death|passes away|passed away|passing|demise|funeral|last rites|cremation|mourns|mourning|mourn|"
                   r"tribute to (the )?late|late (friend|actor|director|singer)|condolences?|rip|shraddhanjali|final journey)\b")
SENSITIVE = re.compile(r"(?i)fatwa|blasphem|communal|riot|jihad|conversion|namaz|mosque|masjid|azaan|\bbeef\b|lynch|boycott|hate speech|"
                       r"religious sentiments|hurt(ing)? sentiments|offend(s|ed|ing)? (hindu|muslim|christian|sikh)|anti-hindu|anti-muslim")
AMBIGUOUS = {"king", "yash", "vijay", "shah", "srk"}        # words that are not always a star
ALIASES = {"srk": "shah rukh", "shahrukh": "shah rukh", "shah ruk": "shah rukh"}
PEAK_SLOTS = {"reel_news_am", "reel_news_pm"}   # 12 PM and 4 PM: the best hours (avg 1,808 / 1,829 views since 20 Oct 2026): pins go there first when they fit
SERIES = ("koffee with karan", "kapil sharma show", "bigg boss")       # reliable series: Koffee With Karan 2,715 avg, Ramayana 1,519 avg (bar 2 sites in 6 h since 19 Nov 2026; Ramayana removed 19 Nov 2026: a film, not an episode series, its news is mostly promos)
NEWS_SLOTS = ("reel_news_early", "reel_break_am", "reel_news_am", "reel_break_noon", "reel_news_pm", "reel_break_night")

SERIES_KEY = {"koffee with karan": "karan", "kapil sharma show": "kapil", "bigg boss": "bigg boss"}   # the word that must be in a headline for the series (19 Nov 2026)


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
            if (now - d).total_seconds() <= (6 if name in SERIES else 4) * 3600 and SERIES_KEY.get(name, name.split()[-1]) in a["title"].lower() and not emergency.SKIP_WORDS.search(a["title"]) \
                    and not SENSITIVE.search(a["title"]) and not DEATH.search(a["title"]) and not PROMO.search(a["title"]) and not ROUTINE.search(a["title"]):
                sites.add(a["site"])
                arts.append(a)
        need = 2 if name in SERIES else 4 if name in trend_text else 6   # 19 Nov 2026 (owner: the Ananya Birla Koffee With Karan stories were missed at 12:30 and 2:30): series bar 2 sites, 6 h
        print(f"  flash check: {name}: {len(sites)} sites in {6 if name in SERIES else 4} h (need {need})")
        if len(sites) >= need:
            score = len(sites) + (3 if name in trend_text else 0) + (20 if name in SERIES else 0) + (2 if name == "koffee with karan" else 1 if name == "kapil sharma show" else 0)   # a Koffee With Karan / Ramayana story beats a plain pile-up (19 Nov 2026: a 'Men's Day special' list had won)
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


# 🗳️ Big politics door (owner 10 Nov 2026): politics is blocked in the news / breaking pickers (reel.drop_routine), except ONE huge, safe story a day.
OPPOSITION = re.compile(r"(?i)\b(rahul gandhi|priyanka gandhi|sonia gandhi|congress|kharge|kejriwal|aap|mamata|tmc|trinamool|akhilesh|tejashwi|owaisi|pawan khera|"
                        r"jairam ramesh|samajwadi|rjd|left front|cpi|cpm|uddhav|sharad pawar|stalin|dmk|opposition|inc)\b")
BAD_NEWS = re.compile(r"(?i)\b(arrest|arrested|scam|scandal|fir|probe|raid|raids|backlash|slams|slammed|row|controversy|violence|clash|clashes|killed|attack|"
                      r"blast|stampede|collapse|riot|protest|resign|resigns|resignation|setback|embarrass|trolled|gaffe)\b")
STOP = set("the a an of to in on for and with from by at as is are was were be has have had will after over into about says said say new its his her their "
           "this that than they them you your how why what when who whom not can could would may might more most also just very still".split())


def sig_words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", t.lower()) if w not in STOP}


def detect_big_politics(state: dict, now_ist: datetime, cands: list[dict], trends: list) -> dict | None:
    """A politics story that 8+ different news sites run in the last 4 h (5+ when it also matches an X trend) and that is safe for the page:
    not an opposition-led story, no arrest / scam / violence / controversy words, not religious-conflict, not a death story. At most once a day.
    The picker may still choose another story (it is only listed first, marked HOT)."""
    import reel
    import create_post
    if state.get("big_politics_day") == now_ist.date().isoformat():
        return None
    trend_words = set()
    for t, _ in trends[:80]:
        trend_words |= sig_words(t)
    cutoff = time.time() - 6 * 3600
    pool = [c for c in cands if c.get("ts", 0) >= cutoff and reel.is_politics(c) and not OPPOSITION.search(c["title"])
            and not BAD_NEWS.search(c["title"]) and not SENSITIVE.search(c["title"]) and not DEATH.search(c["title"]) and not ROUTINE.search(c["title"])]
    pool.sort(key=lambda c: -c["ts"])
    now = datetime.now(timezone.utc)
    best = None
    for c in pool[:4]:
        words = sig_words(c["title"])
        if len(words) < 3 or reel.covered_before(c["title"], 12):
            continue
        sites, arts = {c["source"]}, [c]
        query = " ".join(sorted(words, key=lambda w: -len(w))[:6])
        for a in create_post.bing_articles(query, limit=15, newest=True):
            d = bing_dt(a)
            if d is None or (now - d).total_seconds() > 4 * 3600:
                continue
            tw = sig_words(a["title"])
            if len(tw & words) >= max(3, int(0.4 * len(words))) and not OPPOSITION.search(a["title"]) and not BAD_NEWS.search(a["title"]):
                sites.add(a["site"])
        need = 5 if len(words & trend_words) >= 2 else 8
        print(f"  big-politics check: {c['title'][:60]}: {len(sites)} sites in 4 h (need {need})")
        if len(sites) >= need and (not best or len(sites) > best[0]):
            best = (len(sites), c)
    if not best:
        return print("  big politics: nothing huge right now") or None
    return {"title": best[1]["title"], "link": best[1]["link"], "source": best[1]["source"], "sites": best[0]}


# ---- 🌋 MEGA STORY door (owner 17 Nov 2026: 'Sonia Gandhi's book launch / Dhoni's IPL retirement: very big stories must not be missed') ----
GRAPHIC = re.compile(r"(?i)\b(rape|raped|gang[- ]?rape|molest\w*|sexual(ly)? (assault|abuse)|child abuse|minor girl|POCSO|beheaded|dismember\w*|mutilat\w*|suicide|self[- ]harm|massacre|"
                     r"terror(ist)? attack|bomb blast)\b")
GOVT = re.compile(r"(?i)\b(bjp|nda|modi|amit shah|yogi|nadda|rajnath|union minister|centre|pmo)\b")
ENT_SKIP = set("india indian today news live update updates latest breaking world after before while with from this that over year years also says said monday tuesday wednesday thursday friday saturday sunday "
               "january february march april june july august september october november december people video photos watch reports report first big best week".split())
MEGA_SITES = 10      # different sites in 6 h (owner 17 Nov 2026)
MEGA_SITES_TREND = 5  # when 2+ of the story's words also trend on X / Google India
MEGA_SITES_ENT = 8    # a name shared by 4+ sites' headlines (entity pass)
MEGA_SITES_OTHER = 9   # non-Bollywood topics (cricket, politics, business...): never below 9 sites, even when trending
MEGA_SITES_NAME = 7   # when 1 word (a name, e.g. 'dhoni') trends: 17 Nov 2026 the Dhoni IPL-retirement story had 9 sites


DISCOVERY = ("top stories today", "AI news", "stock market news", "breaking news India", "India news today", "business news India", "technology news", "world news", "cricket news", "Bollywood news")


def bing_discovery(hours: int = 6) -> list[dict]:
    """Newest Bing News headlines for a few broad searches, as candidate stories ({title, link, source, ts, category}); [] on any failure."""
    import create_post
    from concurrent.futures import ThreadPoolExecutor
    now = datetime.now(timezone.utc)

    def one(q):
        try:
            return create_post.bing_articles(q, limit=25, newest=True)
        except Exception:
            return []
    out, seen = [], set()
    with ThreadPoolExecutor(max_workers=6) as ex:
        for arts in ex.map(one, DISCOVERY):
            for a in arts:
                d = bing_dt(a)
                if d is None or (now - d).total_seconds() > hours * 3600 or not a.get("title") or a["title"] in seen:
                    continue
                seen.add(a["title"])
                out.append({"id": "", "title": a["title"], "link": a.get("url", ""), "source": a.get("site", ""), "summary": "", "category": "viral", "ts": d.timestamp(), "image": ""})
    return out


GN_BASE = "https://news.google.com/rss"
GN_SECTIONS = {"top": "", "entertainment": "ENTERTAINMENT", "sports": "SPORTS", "business": "BUSINESS", "world": "WORLD", "tech": "TECHNOLOGY"}
_GN_CACHE: dict = {"t": 0, "items": []}


def gnews_top(top: int = 25) -> list[dict]:
    """Google News (public RSS, India edition; owner 17 Nov 2026): its Top stories and Entertainment / Sports / Business / World / Technology lists are ordered by how much a story is being
    covered right now. Returns [{title, rank, section}] (cached 10 minutes); [] on any failure. Only for ranking: the links are Google redirects, so the article itself comes from our feeds / Bing."""
    import feedparser
    import requests
    if time.time() - _GN_CACHE["t"] < 600 and _GN_CACHE["items"]:
        return _GN_CACHE["items"]
    out = []
    for name, sec in GN_SECTIONS.items():
        url = f"{GN_BASE}/headlines/section/topic/{sec}?hl=en-IN&gl=IN&ceid=IN:en" if sec else f"{GN_BASE}?hl=en-IN&gl=IN&ceid=IN:en"
        try:
            r = requests.get(url, headers={"User-Agent": "khabarbawaal-studio news reader"}, timeout=15)
            for i, e in enumerate(feedparser.parse(r.content).entries[:top], 1):
                t = re.sub(r"\s+-\s+[^-]{2,40}$", "", e.get("title", "")).strip()   # drop the ' - Source' tail
                if t:
                    out.append({"title": t, "rank": i, "section": name})
        except Exception as ex:
            print(f"  ! google news {name} skipped: {str(ex)[:60]}")
    if out:
        _GN_CACHE.update(t=time.time(), items=out)
    return out


def gnews_match(title: str, items: list[dict]) -> dict | None:
    """The best Google News item that is the same story as `title` (half or more of the headline's key words, at least 3), or None."""
    w = sig_words(title)
    if len(w) < 3:
        return None
    best = None
    for it in items:
        iw = sig_words(it["title"])
        if len(w & iw) >= max(3, int(0.5 * min(len(w), len(iw)))) and (best is None or it["rank"] < best["rank"]):
            best = it
    return best


def site_count(title: str, hours: int = 6) -> int:
    """How many different sites ran this story in the last `hours` (Bing News, headline words); 1 when unknown."""
    import create_post
    words = sig_words(title)
    if len(words) < 3:
        return 1
    now = datetime.now(timezone.utc)
    sites = set()
    try:
        arts = create_post.bing_articles(" ".join(sorted(words, key=lambda w: -len(w))[:6]), limit=20, newest=True)
    except Exception:
        return 1
    for a in arts:
        d = bing_dt(a)
        if d is None or (now - d).total_seconds() > hours * 3600:
            continue
        if len(sig_words(a["title"]) & words) >= max(3, int(0.4 * len(words))):
            sites.add(a["site"])
    return max(1, len(sites))


def annotate_sites(stories: list, top: int = 30, hours: int = 6) -> None:
    """Puts story['sites'] on the first `top` stories (threads, no AI): the picker sees how many sites run each one (owner 17 Nov 2026: 'the 12-sites approach for all Reels')."""
    from concurrent.futures import ThreadPoolExecutor
    todo = [s for s in stories[:top] if "sites" not in s]
    if not todo:
        return
    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for s, n in zip(todo, ex.map(lambda x: site_count(x["title"], hours), todo)):
                s["sites"] = n
    except Exception as e:
        print(f"  ! site count skipped: {str(e)[:80]}")


INDIAN_SITES = ("times of india", "hindustan times", "ndtv", "india today", "news18", "indian express", "the hindu", "mint", "economic times", "business standard", "firstpost", "abp", "zee", "moneycontrol",
                "deccan", "scroll", "dna", "republic", "outlook", "cnbctv18", "cnbc-tv18", "cnbc tv18", "tribune", "telegraph india", "financial express", "theprint", "free press", "mid-day", "pinkvilla",
                "bollywood", "koimoi", "india.com", "oneindia", "navbharat", "dainik", "jagran", "amar ujala", "bhaskar", "sportstar", "cricbuzz", "espncricinfo", "wion", "news9", "quint", "jansatta",
                "lokmat", "pti", "ians", "ani news", "livemint", "the week", "iamgujarat", "tv9", "aaj tak", "hindi", "sakshi", "manorama", "gadgets 360", "gadgets360", "inc42", "entrackr", "bw ", "kalinga")
MEGA_INDIAN_SITES = 5   # non-Bollywood mega stories must be run by 5+ INDIAN outlets (owner 17 Nov 2026: 'big stories like Dhoni, Sonia, Nvidia'; a US 'Rubio / tech outages' story with 10 foreign sites passed on 18 Nov)


def indian_count(sites) -> int:
    return sum(1 for x in sites if any(k in x.lower() for k in INDIAN_SITES))


def emergency_stars(c: dict) -> bool:
    import emergency
    return c.get("category") == "entertainment" or bool(emergency.STARS.search(c["title"]))


def detect_mega(state: dict, now_ist: datetime, cands: list[dict], trends: list, gtrends: list | None = None, hours: int = 6) -> dict | None:
    """🌋 A story of ANY topic (politics, cricket, a star, an event) that 10+ different sites ran in the last 6 h (5+ when its words also trend on X or Google India): 'the talk of today'.
    Not limited to once a day. Never: religious conflict, a story that embarrasses the BJP / government, a greeting, a story our Reels
    already told in the last 24 h. Deaths and crimes are allowed (the writer is told to keep a respectful, facts-only tone). The picker makes it the Reel when it exists."""
    import reel
    import create_post
    trend_words = set()
    for t, _ in trends[:80]:
        trend_words |= sig_words(t)
    for t in gtrends or []:
        trend_words |= sig_words(t)
    try:
        gn = [it for it in gnews_top(25) if it["rank"] <= 10]
        gn15 = [it for it in gnews_top(25) if it["rank"] <= 15]   # a story in the first 10 places of a Google News list counts as trending (the story itself must match, not just share words)
    except Exception:
        gn, gn15 = [], []
    cutoff = time.time() - hours * 3600
    cands = list(cands) + bing_discovery(hours)   # stories our own feeds do not carry (17 Nov 2026: the Nvidia / Jensen Huang scandal was in none of them)
    pool = [c for c in cands if c.get("ts", 0) >= cutoff and not SENSITIVE.search(c["title"]) and not ROUTINE.search(c["title"])
            and not (GOVT.search(c["title"]) and BAD_NEWS.search(c["title"]))]
    # cheap first pass: stories that several of our own feeds already share words with
    sw = {id(c): sig_words(c["title"]) for c in pool}
    def feeds(c):
        w = sw[id(c)]
        return len({o["source"] for o in pool if len(w) >= 3 and len(w & sw[id(o)]) >= max(3, int(0.5 * len(w)))})
    pool.sort(key=lambda c: (-feeds(c), -c.get("ts", 0)))
    now = datetime.now(timezone.utc)
    best, seen_words = None, []
    for c in pool[:30]:
        words = sw[id(c)]
        if len(words) < 3 or any(len(words & x) >= max(3, int(0.6 * len(words))) for x in seen_words):
            continue
        seen_words.append(words)
        if len(seen_words) > 14 or reel.covered_before(c["title"], 24) or reel.told_in_days(c["title"], 3):
            continue
        sites, arts = {c["source"]}, [c]
        query = " ".join(sorted(words, key=lambda w: -len(w))[:6])
        for a in create_post.bing_articles(query, limit=20, newest=True):
            d = bing_dt(a)
            if d is None or (now - d).total_seconds() > hours * 3600:
                continue
            tw = sig_words(a["title"])
            if len(tw & words) >= max(3, int(0.4 * len(words))) and not SENSITIVE.search(a["title"]):
                sites.add(a["site"])
        nt = len(words & trend_words)
        if gn and gnews_match(c["title"], gn):
            nt = max(nt, 2)
        if nt >= 1:   # the story is worded differently on every site: also count the sites that run the TREND's own words (e.g. 'Sonia Gandhi' for 'Rahul ... at Sonia Gandhi's book launch')
            tl = [t for t, _ in trends[:80]] + list(gtrends or [])
            tw_best = next((sig_words(t.lstrip("#")) for t in tl if sig_words(t.lstrip("#")) and sig_words(t.lstrip("#")) <= words | {w for x in sw.values() for w in x} and sig_words(t.lstrip("#")) & words), None)
            if tw_best:
                for a in create_post.bing_articles(" ".join(sorted(tw_best)), limit=25, newest=True):
                    d = bing_dt(a)
                    if d is not None and (now - d).total_seconds() <= hours * 3600 and tw_best <= sig_words(a["title"]) and not SENSITIVE.search(a["title"]):
                        sites.add(a["site"])
        need = MEGA_SITES_TREND if nt >= 2 else MEGA_SITES_NAME if nt == 1 else MEGA_SITES
        import emergency
        if not (c.get("category") == "entertainment" or emergency.STARS.search(c["title"])):
            need = max(need, MEGA_SITES_OTHER)   # owner 17 Nov 2026: Bollywood first; any other topic only when it is REALLY big
        print(f"  mega check: {c['title'][:60]}: {len(sites)} sites in {hours} h (need {need}, {nt} trend words)")
        star = emergency_stars(c)
        if star:
            need = MEGA_SITES   # Bollywood: the normal picker handles ordinary stories; only a 10+ site story is forced
        indian_ok = star or (indian_count(sites) >= MEGA_INDIAN_SITES and bool(gn15 and gnews_match(c["title"], gn15)))   # non-Bollywood: Indian outlets AND a Google News top-15 place
        if len(sites) >= need and indian_ok and (not best or len(sites) > best[0]):
            best = (len(sites), c)
    # entity pass (17 Nov 2026, the Nvidia / Jensen Huang scandal): a name that headlines of 4+ different sites share, counted by Bing; titles must share the name AND another word with the story's headline
    from collections import defaultdict
    ent_src = defaultdict(set)
    ent_rep = {}
    for c in pool:
        for w in set(re.findall(r"\b[A-Z][a-z]{3,}\b", c["title"])):
            lw = w.lower()
            if lw in STOP or lw in ENT_SKIP:
                continue
            ent_src[lw].add(c["source"])
            if lw not in ent_rep or c.get("ts", 0) > ent_rep[lw].get("ts", 0):
                ent_rep[lw] = c
    for ent, srcs in sorted(ent_src.items(), key=lambda kv: -len(kv[1]))[:6]:
        rep = ent_rep[ent]
        if len(srcs) < 4 or reel.covered_before(rep["title"], 24) or reel.told_in_days(rep["title"], 3) or any(ent in x for x in seen_words):
            continue
        words = sig_words(rep["title"])
        sites = set()
        for a in create_post.bing_articles(ent, limit=25, newest=True):
            d = bing_dt(a)
            if d is None or (now - d).total_seconds() > hours * 3600 or SENSITIVE.search(a["title"]):
                continue
            tw = sig_words(a["title"])
            if ent in tw and len(tw & words) >= 2:
                sites.add(a["site"])
        print(f"  mega entity check: {ent}: {len(sites)} sites ({indian_count(sites)} Indian) in {hours} h (need {MEGA_SITES_ENT})")
        if len(sites) >= (MEGA_SITES if emergency_stars(rep) else MEGA_SITES_OTHER) and (emergency_stars(rep) or (indian_count(sites) >= MEGA_INDIAN_SITES and bool(gn15 and gnews_match(rep["title"], gn15)))) and not (GOVT.search(rep["title"]) and BAD_NEWS.search(rep["title"])) and (not best or len(sites) > best[0]):
            best = (len(sites), rep)
    if not best:
        return print("  mega story: nothing huge right now") or None
    return {"title": best[1]["title"], "link": best[1]["link"], "source": best[1]["source"], "sites": best[0], "category": best[1].get("category", "")}


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
        mg = detect_mega(state, now_ist, cands, fun.x_trends() or [], fun.google_trends() or [])
        if mg:
            out.insert(0, {"id": "", "source": mg["source"], "category": mg["category"] or "viral", "title": mg["title"], "summary": "", "link": mg["link"],
                           "image": "", "ts": time.time(), "hot": f"MEGA: {mg['sites']} sites run it", "mega": True})
    except Exception as e:
        print(f"  ! mega check failed: {type(e).__name__}: {str(e)[:100]}")
    try:
        bp = None   # 19 Nov 2026: the old 'big politics' door is off (the mega door, with its Indian-outlet and Google News checks, covers really big stories; it had listed a FIFA election story)
        if bp:
            out.append({"id": "", "source": bp["source"], "category": "politics", "title": bp["title"], "summary": "", "link": bp["link"],
                        "image": "", "ts": time.time(), "hot": f"BIG POLITICS: {bp['sites']} sites run it right now"})
    except Exception as e:
        print(f"  ! big politics check failed: {type(e).__name__}: {str(e)[:100]}")
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
