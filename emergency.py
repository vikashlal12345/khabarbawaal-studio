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
        reel.SERIES_KIND = kind if kind in reel.NEWS_KINDS or kind in reel.BREAK_KINDS else "reel_news"   # satire / joke slots: a news card
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


# ------------------------------------------------------------------ 🙏 devotional (6 AM) without AI
def _devotion_data() -> dict:
    import json
    return json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "auto_devotion.json"), encoding="utf-8"))


def _painting(queries: list[str], state: dict, tmp: str, n: int = 2) -> list[tuple]:
    """Free Wikimedia Commons paintings chosen without AI (title says painting / miniature): [(img, credit, focus_x)]."""
    import io
    import time
    import requests
    import reel
    used = state.get("devotion_images_used", [])
    found = reel.commons_images(queries)
    cands = [c for c in found if c["title"] not in used] or found
    cands = [c for c in cands if any(w in c["title"].lower() for w in ("painting", "miniature", "ravi varma", "illustration", "lithograph"))
             and not re.search(r"(?i)\b(statue|temple|idol|book|cover|manuscript|page|stamp|coin|poster)\b", c["title"])]
    cands.sort(key=lambda c: -min(c["w"], c["h"]))
    ua = {"User-Agent": "KhabarBawaalStudio/1.0 (github.com/vikashlal12345)"}
    out = []
    for c in cands[:8]:
        try:
            time.sleep(2.0)
            r = requests.get(c["url"], headers=ua, timeout=30)
            if r.status_code == 429:
                time.sleep(6)
                r = requests.get(c["url"], headers=ua, timeout=30)
            r.raise_for_status()
            img = reel.Image.open(io.BytesIO(r.content))
            img.load()
        except Exception as e:
            print(f"  ! painting skipped: {str(e)[:80]}")
            continue
        out.append((img.convert("RGB"), c["credit"], 0.5, c["title"]))
        if len(out) >= n:
            break
    if out:
        state["devotion_images_used"] = (used + [o[3] for o in out])[-30:]
    return [o[:3] for o in out]


def make_devotion(kind: str, state: dict, feed: list) -> bool:
    """A no-AI 🙏 6 AM devotional Reel: written blessing (assets/auto_devotion.json), a free Wikimedia painting, free songs."""
    import reel
    import schedule
    import status
    from datetime import date
    today = schedule.ist_now().date().isoformat()
    status.start(kind, today, "⚙️ Auto mode · 6 AM devotional", 4, "Claude's limit is reached: making a Reel without AI")
    try:
        data = _devotion_data()
        day = date.fromisoformat(today)
        subj = reel.devotion_subject(day)
        status.step("Choosing today's blessing", 2)
        cm = data["navratri_common"]
        if subj["kind"] == "navratri" and subj["deity"] in data["navratri"]:
            d = data["navratri"][subj["deity"]]
            head, cta, seekh, tags, songs_q = cm["head"], cm["cta"], cm["seekh"], cm["tags"], cm["songs"]
            middle, label = d["middle"], subj["name"].split(":")[0]
        else:
            deity = subj["deity"]
            if subj["kind"] == "festival":
                low = subj["name"].lower()
                deity = next((v for k, v in data["festival_deity"].items() if re.search(k, low)), "")
            deity = deity or reel.WEEKDAY_GODS[day.weekday()]
            d = data["weekday"].get(deity) or data["extra"].get(deity) or data["weekday"][reel.WEEKDAY_GODS[day.weekday()]]
            head, cta, seekh, tags, songs_q = d["head"], d["cta"], data["defaults"]["seekh"], d["tags"], d["songs"]
            middle, label = d["middle"], ""
        status.step("Finding a free painting", 3)
        fam = d["family"]
        banked = reel.load_god_bank(deity if subj["kind"] != "navratri" else subj["deity"], fam, today)   # an earlier AI picture first
        images = (banked[0] if banked else None) or _painting(d["searches"], state, tempfile.gettempdir(), 2) or \
            _painting([f"{fam} painting", f"{fam} Raja Ravi Varma", f"{fam} miniature painting"], state, tempfile.gettempdir(), 2)
        if not images:
            status.fail("Auto mode: no free painting of the deity was found")
            return False
        songs = reel.devotional_songs(songs_q)[:4]
        status.step("Making the video", 4)
        hook, lesson, real_life = d["hook"], d["lesson"], d["real_life"]
        highlight = [w for w in re.findall(r"(?:Maa|Shri|Bhagwan|Hanuman|Ganesh|Shiv|Ram|Krishna|Lakshmi|Surya|Shani|Vishnu|Mahadev|Bajrangbali|Ganpati)\s?\w*", hook)][:2]
        labels = {"head": head, "middle": middle, "seekh": seekh, "cta": cta, "source": f"—  {label}" if label else ""}
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "reel.mp4")
            reel.SERIES_KIND = kind
            frames = reel.gita_reel(images, "", hook, lesson, real_life, highlight, out, labels=labels)
            credits = "; ".join(dict.fromkeys(c for _, c, _ in images))
            hashtags = " ".join(f"#{t}" for t in tags + ["bhakti", "devotional", "india"])
            caption = (f"{hook}\n{lesson}\n\n{cta} 🙏\n\nPainting: {credits}\nFollow {g.CONFIG['handle']} for daily darshan 🙏\n\n{hashtags}")
            proof = {"state": "check", "fixed": [], "issues": [
                "⚙️ AUTO MODE: Claude's limit was reached. This blessing was written in advance (traditional facts only) and the "
                "painting was picked without AI: please look at the painting before posting."]}
            reel.save_reel(feed, state, frames[1], out, f"{kind}-{today}", {
                "headline": f"{subj['name']}: {hook}"[:140], "tag": "🎬 REEL · ⚙️ AUTO MODE (no AI) · 🙏 " + head,
                "caption": g.limit_hashtags(caption), "source": "KhabarBawaal Original", "source_url": "",
                "pin_comment": data["defaults"]["pin"],
                "songs": [{k: s_[k] for k in ("title", "artist", "lang", "mood")} for s_ in songs], "proof": proof})
        return True
    except Exception as e:
        print(f"  ! auto devotion failed: {type(e).__name__}: {str(e)[:200]}")
        status.fail(f"Auto mode failed: {str(e)[:160]}")
        return False


# ------------------------------------------------------------------ 🎥🔊 speaker video (9:30 PM) without AI
NAMES = {"narendramodi": "MODI JI", "amitshah": "AMIT SHAH", "rajnathsingh": "RAJNATH SINGH", "pmoindia": "PM MODI",
         "bjp4india": "BJP", "jpnadda": "JP NADDA", "drsjaishankar": "JAISHANKAR", "nitin_gadkari": "GADKARI",
         "myogiadityanath": "YOGI JI", "dev_fadnavis": "FADNAVIS", "himantabiswa": "HIMANTA", "pib_india": "SARKAR",
         "anupampkher": "ANUPAM KHER", "sirpareshrawal": "PARESH RAWAL", "kanganateam": "KANGANA", "vivekagnihotri": "VIVEK AGNIHOTRI",
         "pradip103": "PRADIP BHANDARI", "shehzad_ind": "SHEHZAD", "sambitswaraj": "SAMBIT PATRA", "amitmalviya": "AMIT MALVIYA",
         "annamalai_k": "ANNAMALAI", "tejasvi_surya": "TEJASVI SURYA", "smritiirani": "SMRITI IRANI", "kapilmishra_ind": "KAPIL MISHRA"}
SPEECH_SKIP = re.compile(r"(?i)\b(wish|wishes|greeting|greetings|happy|shubh|badhai|congratulat|condolence|tribute|rip|"
                         r"shraddhanjali|bhavpurn|punyatithi|jayanti|navratri|diwali|dussehra|durga|puja|birthday|janmadin|"
                         r"died|death|demise|passed away|accident|killed|attack|terror|trailer|teaser|song|movie|film)\b")


def make_speaker(kind: str, state: dict, feed: list) -> bool:
    """A no-AI 🎥🔊 9:30 PM speaker video: the strongest-looking recent video of a leader's own X account (a plain
    'NAME KA BAYAN' headline, his voice, no subtitles). Greetings / tributes / film posts are skipped by keywords."""
    import reel
    import requests
    import schedule
    import status
    today = schedule.ist_now().date().isoformat()
    status.start(kind, today, "⚙️ Auto mode · 9:30 PM speaker video", 4, "Claude's limit is reached: making a Reel without AI")
    try:
        status.step("Finding a leader's own video on X", 2)
        used = set(state.get("reel_official_used", []))
        vids = [v for v in reel.official_videos(used, hours=72, max_wait=960) if 20 <= v["secs"] <= 300 and not SPEECH_SKIP.search(v["text"])
                and roman(v["text"]) and v["handle"].lower() in NAMES]
        if not vids:
            status.fail("Auto mode: no suitable speaker video was found on X")
            return False
        v = vids[0]   # newest first
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "speaker.mp4")
            open(src, "wb").write(requests.get(v["mp4"], timeout=120).content)
            length = reel.video_info(src)[2]
            start = 1.0
            secs = min(35.0, length - start - 0.5)
            if secs < 12:
                status.fail("Auto mode: the speaker video was too short")
                return False
            name = NAMES[v["handle"].lower()]
            headline = ["AAJ KA KADAK BAYAN", "Poora video dekhein"]   # no AI: who speaks is not known, so no name
            status.step("Making the video", 4)
            out = os.path.join(tmp, "speech.mp4")
            reel.SERIES_KIND = kind
            frames = reel.speech_reel(src, start, secs, headline, [], out, question="Aap sehmat ho? Comment karo")
            voiced = os.path.join(tmp, "voiced.mp4")
            reel.add_voice(out, src, start, voiced, loud=True)
            said = v["text"][:220]
            credit = f"Video + voice: posted by @{v['handle']} on X"
            tags = " ".join(f"#{t}" for t in ["india", "trending", "reelsindia", "viral", re.sub(r"\W", "", name.lower())])
            caption = (f"@{v['handle']} ne ye video share kiya:\n{said}\n\nAap kya sochte ho? Comment karo 👇\n\n{credit}\n"
                       f"Follow {g.CONFIG['handle']} for daily updates.\n\n{tags}")
            proof = {"state": "check", "fixed": [], "issues": [
                "⚙️ AUTO MODE: Claude's limit was reached. The video posted by a leader's X account is used as it is (the speaker may be someone else, so the headline names nobody), with "
                "no subtitles: please watch it before posting (no AI checked it)."]}
            state["reel_official_used"] = (list(used) + [v["id"]])[-60:]
            reel.save_reel(feed, state, frames[0], voiced, f"{kind}-{today}", {
                "headline": " · ".join(headline), "tag": "🎬 REEL · ⚙️ AUTO MODE (no AI) · 🎥🔊 SPEAKER VIDEO",
                "caption": g.limit_hashtags(caption), "source": f"@{v['handle']} on X",
                "source_url": f"https://x.com/{v['handle']}/status/{v['id']}", "pin_comment": "Kisi dost ko bhejo 👇",
                "songs": [], "proof": proof})
        return True
    except Exception as e:
        print(f"  ! auto speaker failed: {type(e).__name__}: {str(e)[:200]}")
        status.fail(f"Auto mode failed: {str(e)[:160]}")
        return False
