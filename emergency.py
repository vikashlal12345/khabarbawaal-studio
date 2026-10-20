"""⚙️ Emergency mode (owner, 20 Oct 2026): when Claude's plan limit is used up, the 🔥 news and ⚡ breaking slots still get a
Reel, made WITHOUT any AI from free sources: the news feeds, X trends (trends24.in), and the article's own photos.

Every word on the Reel is copied from the article (headline, sentences), nothing is written or invented, fixed
question lines are used, politics / crime / deaths are skipped (no AI to judge them), and the Reel is marked
'⚙️ AUTO MODE' in the app with a check-before-posting note.

    emergency.make_reel(kind, state, feed) -> bool
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone

import generate as g

ALLOWED = {"viral", "entertainment", "cricket", "sports", "tech", "funny/offbeat"}
SKIP_WORDS = re.compile(
    r"(?i)\b(bjp|congress|aap|tmc|cpi|modi|rahul|kejriwal|mamata|yogi|shah|opposition|minister|election|poll|parliament|"
    r"lok sabha|rajya sabha|supreme court|high court|court|cbi|ed|police|arrest|arrested|murder|killed|kill|dies|dead|death|"
    r"died|accident|crash|rape|attack|blast|terror|firing|shot|shooting|stabbed|suicide|tragedy|protest|riot|riots|bail|"
    r"jail|ukraine|gaza|war)\b")
QUESTIONS = ["Aapka kya kehna hai? Comment karo", "Sahi ya galat? Comment karo", "Aap kiske saath ho? Comment karo",
             "Kya ye sahi hai? Haan ya Na? Comment karo", "Aapko ye kaisa laga? Comment karo"]
HEADLINES = {"entertainment": "FILM NEWS", "cricket": "CRICKET UPDATE", "sports": "SPORTS UPDATE", "tech": "TECH UPDATE",
             "viral": "VIRAL NOW", "funny/offbeat": "VIRAL NOW"}
NOISE = re.compile(r"(?i)(also read|read more|follow us|click here|subscribe|advertisement|sign up|photo:|image:|"
                   r"copyright|all rights|download the app|whatsapp)")


def roman(text: str) -> bool:
    return not re.search(r"[֐-ࣿऀ-෿]", text)


def sentences(text: str) -> list[str]:
    """Sentences of the article, 5-16 words, no menu / 'also read' noise, Roman letters only."""
    out = []
    for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text)):
        s = s.strip(" \"'")
        n = len(s.split())
        if 5 <= n <= 16 and roman(s) and not NOISE.search(s) and s[0].isupper():
            out.append(s if s[-1] in ".!?" else s + ".")
    return list(dict.fromkeys(out))


def clean_title(title: str) -> str:
    return re.split(r"\s+[|\-–—]\s+(?=[A-Z][\w .&]+$)", title)[0].strip()


def hook_of(title: str) -> str:
    """The headline cut to at most 9 words (at a colon if there is one): the exact words, never reworded."""
    t = clean_title(title)
    if ":" in t and len(t.split(":")[0].split()) >= 3:
        t = t.split(":")[0]
    words = t.split()
    return " ".join(words[:9]) + ("..." if len(words) > 9 else "")


def photos_of(link: str) -> list[tuple]:
    """Up to 3 sharp photos of the article: [(img, site)], real photos only (no logos or tiny images)."""
    import carousel
    page = carousel.page_html(link)
    site = re.sub(r"^www\.", "", re.sub(r"https?://", "", link).split("/")[0])
    urls = []
    og = g.og_image(link)
    if og:
        urls.append(og)
    urls += [u for u in carousel.article_image_urls(link, page, limit=8)
             if not re.search(r"(?i)author|avatar|profile|byline|logo|icon|user|columnist|sprite|placeholder", u)]
    out, seen = [], set()
    for u in dict.fromkeys(urls):
        img = g.download_image(u)
        if img is None or min(img.size) < 480:
            continue
        key = img.convert("L").resize((8, 8)).tobytes()
        if key in seen:
            continue
        seen.add(key)
        out.append((img.convert("RGB"), site))
        if len(out) >= 3:
            break
    return out   # the article's lead photo (og:image) first: without AI it is the safest cover


def pick_story(state: dict, today: str):
    """The most trending allowed story not used in the last days: (item, sentences, photos) or None."""
    import carousel
    import fun
    import reel
    used = set(state.get("reel_news_used", []))
    recent = [r["title"].lower() for r in reel.recent_news(state, today)[0]]
    trends = []
    try:
        trends = fun.x_trends()
    except Exception:
        pass
    hours = lambda t: max((h for term, h in trends if len(term) >= 4 and term.lower().lstrip("#") in t.lower()), default=0)
    items = [i for i in g.fetch_candidates(set(state.get("seen", [])))
             if i["link"] not in used and i.get("category", "") in ALLOWED and roman(i["title"])
             and not SKIP_WORDS.search(i["title"] + " " + i.get("summary", ""))]
    items.sort(key=lambda i: -hours(i["title"]))
    for it in items[:12]:
        words = set(re.findall(r"[a-z]{4,}", it["title"].lower()))
        if any(len(words & set(re.findall(r"[a-z]{4,}", r))) >= max(3, len(words) * 0.6) for r in recent):
            continue   # the same story as a recent Reel
        text = carousel.article_text(carousel.page_html(it["link"]), limit=3500)
        tw = set(re.findall(r"[a-z]{4,}", it["title"].lower()))
        sents = [s for s in sentences(text) if not SKIP_WORDS.search(s)
                 and len(tw & set(re.findall(r"[a-z]{4,}", s.lower()))) < 0.7 * max(len(tw), 1)]   # not the headline again
        if len(sents) < 3:
            continue
        photos = photos_of(it["link"])
        if photos:
            return it, sents[:4], photos
    return None


def make_reel(kind: str, state: dict, feed: list) -> bool:
    """A no-AI breaking-card Reel for a news / breaking slot. False (reason in ⏳ Activity) if nothing safe was found."""
    import reel
    import schedule
    import status
    today = schedule.ist_now().date().isoformat()
    h, m = schedule.SPECIALS.get(kind, (0, 0))
    status.start(kind, today, f"⚙️ Auto mode · {(h - 1) % 12 + 1}{':%02d' % m if m else ''} {'AM' if h < 12 else 'PM'}", 4,
                 "Claude's limit is reached: making a Reel without AI")
    try:
        status.step("Finding a safe trending story (no politics, crime or deaths)", 2)
        found = pick_story(state, today)
        if not found:
            status.fail("Auto mode: no safe, trending story with photos was found for this slot")
            return False
        it, sents, photos = found
        first, second = photos[0], photos[1] if len(photos) > 1 else photos[0]
        title = clean_title(it["title"])
        cat = it.get("category", "viral")
        bullets = sents[:4]
        question = QUESTIONS[int(hashlib.sha1(title.encode()).hexdigest(), 16) % len(QUESTIONS)]
        hook = hook_of(it["title"])
        status.step("Making the video", 3)
        reel.SERIES_KIND = kind
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "reel.mp4")
            frames = reel.breaking_reel(first[0], second[0], f"Photo: {first[1]}", "BREAKING" if kind in reel.BREAK_KINDS else "UPDATE",
                                        hook, HEADLINES.get(cat, "LATEST UPDATE"), bullets, question, [], out,
                                        cover_window=first[0].width >= first[0].height)   # a wide photo is shown whole
            tags = " ".join(f"#{t}" for t in ["reelsindia", "breakingnews", "trending", "india", re.sub(r"\W", "", cat) or "viral"])
            caption = (f"{title}\n{sents[0]}\n{sents[1]}\n{question} 👇\n\nFollow {g.CONFIG['handle']} for daily updates.\n"
                       f"Source: {it['source']} (link pinned in comments 👇)\n\n{tags}")
            proof = {"state": "check", "fixed": [], "issues": [
                "⚙️ AUTO MODE: Claude's limit was reached, so this Reel was made WITHOUT AI. The words are copied from the "
                "article and nothing was fact-checked or reworded: please read it before posting."]}
            used = state.get("reel_news_used", [])
            state["reel_news_used"] = (used + [it["link"]])[-80:]
            state["reel_news_recent"] = (reel.recent_news(state, today)[0] + [{"title": it["title"], "date": today}])[-40:]
            if it.get("id"):
                state["seen"] = state.get("seen", []) + [it["id"]]
            reel.save_reel(feed, state, frames[0], out, f"{kind}-{today}", {
                "headline": it["title"], "tag": "🎬 REEL · ⚙️ AUTO MODE (no AI)", "caption": g.limit_hashtags(caption),
                "source": it["source"], "source_url": it["link"], "pin_comment": "Kisi dost ko bhejo jo ye jaanna chahta hai 👇",
                "songs": [], "proof": proof})
        return True
    except Exception as e:
        print(f"  ! auto mode failed: {type(e).__name__}: {str(e)[:200]}")
        status.fail(f"Auto mode failed: {str(e)[:160]}")
        return False
