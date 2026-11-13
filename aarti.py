"""🪔 Real aarti video behind the 6 AM God Reel (owner, 12 Nov 2026: 'I want real aarti video of famous temples with the god of the day on top,
not AI pictures; for each day and every festival').

find_footage(plan, subj, today, state, tmp) -> dict | None
    1. official temple X account videos (reel.TEMPLE_X: @Somnath_Temple posts a vertical aarti / darshan video almost every day, with its own sound),
    2. free Pixabay videos (free licence for business use, no credit needed; GitHub secret PIXABAY_API_KEY): searches by the god and the festival,
    each candidate screened by the AI (thumbnails first, then the real 9:16 crop) for: real aarti / puja / temple scene, the right god or no god visible,
    no text / logos (except the temple's own), sharp and bright, subject in the upper 60 % (the text sits below).
    Returns {"seg": 20 s 1080x1920 mp4 (with a sound track), "id", "source", "credit", "caption_credit", "silent", "temple"} or None -> the old AI-picture flow.
Nothing here posts anything; reel.make_aarti_reel renders and saves."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests

import fun
from generate import CONFIG

SEG_SECS = 20.0
LINKS_FILE = Path(__file__).parent / "assets" / "aarti_links.json"   # vetted clip LINKS per god (no video files stored): {"clips": [{id, url, user, tags, w, h, dur, gods: [...]}]}
# festival words -> the gods whose clips fit (the vetted list is tagged by god; 'generic' = aarti / diya footage with no clear idol, fine for any god)
FESTIVAL_GODS = [("diwali|deepavali|dhanteras|bhai dooj|govardhan|narak|lakshmi", ["lakshmi"]), ("navratri|durga|dussehra|dasara|vijayadashami", ["durga"]),
                 ("ganesh|ganpati|vinayaka", ["ganesh"]), ("janmashtami|krishna|vishnu|jagannath|rath yatra", ["vishnu"]), ("hanuman", ["hanuman"]),
                 ("shivratri|shivaratri|sawan|shravan|shiv", ["shiva"]), ("chhath|surya", ["surya"])]
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"}

# Official temple X accounts that post fresh vertical aarti videos (checked 12 Nov 2026; the other temples' accounts show only posts 1-4 years old
# through X's public timeline, so they are not listed). family (lower case) -> [(handle, temple name)]
TEMPLE_X = {"shiva": [("Somnath_Temple", "Shree Somnath Temple")], "shiv": [("Somnath_Temple", "Shree Somnath Temple")],
            "mahadev": [("Somnath_Temple", "Shree Somnath Temple")], "shankar": [("Somnath_Temple", "Shree Somnath Temple")]}

# Pixabay searches per god / festival (English words work best; counts checked 12 Nov 2026: Shani, Surya, Vishnu have almost none -> generic aarti)
FAMILY_QUERIES = {
    "shiva": ["shiva temple", "shivling", "kashi", "varanasi aarti", "ganga aarti"],
    "hanuman": ["hanuman temple", "hanuman", "temple aarti"],
    "ganesh": ["ganesh idol", "ganpati", "ganesh", "temple aarti"],
    "vishnu": ["vishnu", "krishna", "ram mandir", "temple aarti"],
    "krishna": ["krishna", "janmashtami", "temple aarti"],
    "rama": ["ram mandir", "ayodhya", "temple aarti"],
    "lakshmi": ["lakshmi puja", "diwali puja", "diya aarti"],
    "durga": ["durga puja", "navratri", "temple aarti"],
    "shani": ["temple aarti", "diya aarti", "oil lamp"],
    "surya": ["sun rise temple", "ganga aarti", "haridwar aarti"],
}
FESTIVAL_QUERIES = [("diwali|deepavali|dhanteras|bhai dooj|govardhan|narak", ["diwali puja", "diwali diya"]), ("holi|holika", ["holi"]),
                    ("janmashtami", ["janmashtami", "krishna"]), ("navratri|durga|dussehra|dasara|vijayadashami", ["durga puja", "navratri"]),
                    ("ganesh|ganpati|vinayaka", ["ganesh idol", "ganpati"]), ("ram navami|rama", ["ram mandir", "ayodhya"]),
                    ("hanuman", ["hanuman temple", "hanuman"]), ("shivratri|shivaratri|mahashivratri|sawan|shravan", ["shiva temple", "shivling", "kashi"]),
                    ("lakshmi", ["lakshmi puja", "diwali puja"]), ("chhath|surya|sankranti|pongal", ["sun rise temple", "ganga aarti"]),
                    ("kartik|dev deepawali|purnima|ganga", ["ganga aarti", "varanasi aarti"])]
GENERIC_QUERIES = ["temple aarti", "ganga aarti", "varanasi aarti", "haridwar aarti", "diya aarti", "hindu temple"]
FAKE = re.compile(r"(?i)\b(3d|cgi|animat\w*|cartoon\w*|render\w*|illustrat\w*|motion graphics?|abstract|buddha|buddhist|church|mosque|pagoda|christmas)\b")


def _family_key(plan: dict, subj: dict) -> str:
    if subj.get("kind") == "festival":   # a festival's clips follow the festival's own name (Raksha Bandhan got a Krishna temple because the writer's plan told a Krishna story)
        text = " ".join(subj.get("festivals", []) + [subj.get("name", "")]).lower()
    else:
        text = f"{plan.get('family', '')} {plan.get('deity', '')} {subj.get('deity', '')} {subj.get('name', '')}".lower()
    for key, words in (("shiva", ("shiv", "mahadev", "shankar", "bholenath")), ("hanuman", ("hanuman", "bajrang")), ("ganesh", ("ganesh", "ganpati", "vinayak")),
                       ("lakshmi", ("lakshmi", "laxmi")), ("durga", ("durga", "navratri", "devi", "maa ", "mata")), ("shani", ("shani",)),
                       ("surya", ("surya", "sun ")), ("krishna", ("krishna", "janmashtami")), ("rama", ("ram ", "rama", "ayodhya")), ("vishnu", ("vishnu", "narayan"))):
        if any(w in text for w in words):
            return key
    return ""


def queries_for(plan: dict, subj: dict) -> list[str]:
    qs = []
    fest = " ".join(subj.get("festivals", []) + [subj.get("name", "")]).lower()
    for pattern, q in FESTIVAL_QUERIES:
        if subj.get("kind") != "weekday" and re.search(pattern, fest):
            qs += q
    key = _family_key(plan, subj)
    qs += FAMILY_QUERIES.get(key, [])
    qs += GENERIC_QUERIES
    return list(dict.fromkeys(qs))


def _run(cmd: list[str], **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _ffmpeg() -> str:
    import reel
    return reel.ffmpeg_exe()


def _info(path: str) -> tuple[int, int, float, bool]:
    """(width, height, seconds, has_audio)"""
    err = _run([_ffmpeg(), "-i", path]).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    v = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    rot = re.search(r"rotate\s*:\s*(-?\d+)", err)
    w, h = (int(v.group(1)), int(v.group(2))) if v else (0, 0)
    if rot and abs(int(rot.group(1))) in (90, 270):
        w, h = h, w
    return w, h, secs, "Audio:" in err


def pixabay_candidates(queries: list[str], used: set[str]) -> list[dict]:
    """Free Pixabay videos for these searches: sharp enough for a full-screen 9:16 Reel (vertical >= 1080 wide, or 4K landscape cropped to the middle)."""
    key = os.environ.get("PIXABAY_API_KEY", "")
    if not key:
        print("  aarti: no PIXABAY_API_KEY: Pixabay not searched")
        return []
    out, seen = [], set()
    for q in queries[:7]:
        try:
            hits = requests.get("https://pixabay.com/api/videos/", timeout=25, params={"key": key, "q": q, "per_page": 20, "safesearch": "true"}).json().get("hits", [])
        except Exception as e:
            print(f"  ! Pixabay '{q}': {str(e)[:60]}")
            continue
        for h in hits:
            vid = h.get("videos", {})
            big = vid.get("large") or {}
            w, ht = big.get("width") or 0, big.get("height") or 0
            ok_size = (ht > w and w >= 1080) or (w >= 3840) or (ht > w and (vid.get("medium") or {}).get("width", 0) >= 1080)
            pid = f"pb{h['id']}"
            thumb = big.get("thumbnail") or (vid.get("medium") or {}).get("thumbnail") or (vid.get("small") or {}).get("thumbnail") or \
                (f"https://i.vimeocdn.com/video/{h['picture_id']}_640x360.jpg" if h.get("picture_id") else "")
            if pid in seen or pid in used or not ok_size or (h.get("duration") or 0) < 8 or FAKE.search(h.get("tags", "")) or not thumb:
                continue
            seen.add(pid)
            url = big.get("url") if (big.get("url") and w >= 1080) else (vid.get("medium") or {}).get("url")
            out.append({"id": pid, "source": "pixabay", "url": url, "w": w, "h": ht, "dur": h.get("duration"), "tags": h.get("tags", ""), "user": h.get("user", ""),
                        "thumb": thumb, "q": q})
    # vertical first, then 4K landscape; short searches first
    out.sort(key=lambda c: (c["h"] <= c["w"], queries.index(c["q"])))
    return out


def vetted_candidates(plan: dict, subj: dict, used_order: list[str]) -> list[dict]:
    """Clips from the vetted LINK list (assets/aarti_links.json) for today's god / festival, then the generic aarti ones: the god's own clips first, never-used
    first, then the least recently used (a clip returns about once a month with new text)."""
    try:
        clips = json.load(open(LINKS_FILE)).get("clips", [])
    except Exception as e:
        print(f"  aarti: no vetted link list ({str(e)[:50]})")
        return []
    wanted = []
    key = _family_key(plan, subj)
    if key and key != "rama":   # no Ram clip exists: Ram Navami / Ayodhya use the generic ones (never a Krishna temple)
        wanted.append({"krishna": "vishnu"}.get(key, key))
    fest = " ".join(subj.get("festivals", []) + [subj.get("name", "")]).lower()
    if subj.get("kind") != "weekday":
        for pattern, gods in FESTIVAL_GODS:
            if re.search(pattern, fest):
                wanted += [g_ for g_ in gods if g_ not in wanted]
    last_use = {cid: i for i, cid in enumerate(used_order)}
    out = []
    for c in clips:
        gods = c.get("gods", [])
        fests = c.get("festivals", [])
        if subj.get("kind") != "weekday" and any(f and f in fest for f in fests):
            out.append((-1, last_use.get(c["id"], -1), c))   # a clip of THIS festival beats everything
            continue
        rank = next((i for i, w in enumerate(wanted) if w in gods), None)
        if rank is None and "generic" in gods:
            rank = len(wanted)
        if rank is None:
            continue
        out.append((rank, last_use.get(c["id"], -1), c))
    # god-specific first; then never-used / least recently used (so a clip never repeats day after day), then the sharper source (height >= 2000 px = no upscaling)
    out.sort(key=lambda x: (x[0], x[1], (x[2].get("h", 0) or 0) < 2000))
    n_wanted = len(wanted)
    return [{"specific": rank < n_wanted, "id": c["id"], "source": c.get("source", "pixabay"), "url": c["url"], "w": c.get("w", 0), "h": c.get("h", 0), "dur": c.get("dur", 0), "tags": c.get("tags", ""),
             "user": c.get("user", ""), "lic": c.get("lic", ""), "thumb": "", "q": "vetted", "vetted": True, "pid": c["id"][2:]} for rank, _, c in out]


def official_candidates(plan: dict, subj: dict, used: set[str]) -> list[dict]:
    """The god's official temple account on X (videos of the last 4 days, 10 s +), e.g. @Somnath_Temple for Shiva. From GitHub X answers 429 until its 15-minute window resets and the
    address's 30 reads are used up by others straight after: official_videos waits for the reset and asks at once (tested 12 Nov 2026: HTTP 200 on the second try)."""
    import reel
    key = _family_key(plan, subj)
    handles = TEMPLE_X.get(key, [])
    if not handles:
        return []
    names = dict(handles)
    try:
        vids = reel.official_videos(set(), tuple(h for h, _ in handles), hours=96, max_wait=900)
    except Exception as e:
        print(f"  ! X temple videos: {str(e)[:80]}")
        return []
    out = []
    for v in vids:
        cid = f"x{v['id']}"
        if cid in used or v["secs"] < 12 or v["secs"] > 150:
            continue
        out.append({"id": cid, "source": "x", "url": v["mp4"], "handle": v["handle"], "temple": names[v["handle"]], "dur": v["secs"], "tags": v["text"][:120],
                    "user": names[v["handle"]], "thumb": "", "q": "official"})
    return out[:3]


def _download(url: str, path: str, limit_mb: int = 220) -> bool:
    import time
    wiki = "wikimedia.org" in url
    headers = {"User-Agent": "KhabarBawaalStudio/1.0 (github.com/vikashlal12345)"} if wiki else UA   # Wikimedia asks for a contact and answers bursts with 429
    for attempt in range(2 if wiki else 1):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=60) as r:
                if r.status_code == 429 and wiki:
                    time.sleep(6)
                    continue
                if r.status_code != 200:
                    return False
                n = 0
                with open(path, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        n += len(chunk)
                        if n > limit_mb << 20:
                            return False
                        f.write(chunk)
            return os.path.getsize(path) > 50_000
        except Exception as e:
            print(f"  ! download failed: {str(e)[:60]}")
            return False
    return False


def _loudest_start(path: str, secs: float) -> float:
    """Start of the loudest SEG_SECS stretch (the aarti climax: bells, chanting), later stretches preferred on a tie."""
    import statistics
    levels = []
    for t in range(0, max(1, int(secs) - 3), 4):
        err = _run([_ffmpeg(), "-ss", str(t), "-t", "4", "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"]).stderr
        m = re.search(r"mean_volume: (-?[\d.]+) dB", err)
        levels.append((t, float(m.group(1)) if m else -60.0))
    best, best_v = max(0.0, secs - SEG_SECS), -999.0
    span = int(SEG_SECS // 4)
    for i in range(0, max(1, len(levels) - span + 1)):
        v = statistics.mean(x[1] for x in levels[i:i + span]) + 0.001 * i
        if v > best_v:
            best, best_v = float(levels[i][0]), v
    return min(best, max(0.0, secs - SEG_SECS))


def make_segment(src: str, out: str, source: str) -> tuple[str, bool]:
    """A SEG_SECS 1080x1920 H.264 file with a sound track: the clip's own sound (X temple) or silence (stock); vertical = as is, 4K landscape =
    its middle 9:16, a short clip is slowed down (up to 1.4x) and looped to the length."""
    w, h, secs, audio = _info(src)
    start = _loudest_start(src, secs) if audio and secs > SEG_SECS else max(0.0, (secs - SEG_SECS) / 2)
    if h > w:
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    else:
        vf = "crop=ih*9/16:ih,scale=1080:1920"
    avail = min(secs - start, SEG_SECS)
    slow = 1.0
    if avail < SEG_SECS:
        slow = min(1.4, SEG_SECS / max(avail, 1.0))
    vf += ",fps=30"
    if slow > 1.0:
        vf += f",setpts={slow:.3f}*PTS"
    loops = "-1" if avail * slow < SEG_SECS - 0.2 else "0"
    cmd = [_ffmpeg(), "-nostdin", "-loglevel", "error", "-y", "-stream_loop", loops, "-ss", f"{start:.2f}", "-i", src]
    keep_audio = audio and source == "x" and slow == 1.0
    if not keep_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-t", str(SEG_SECS), "-vf", vf, "-map", "0:v", "-map", "0:a" if keep_audio else "1:a", "-c:v", "libx264", "-crf", "17", "-preset", "fast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", out]
    r = _run(cmd)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"segment cut failed: {r.stderr[:200]}")
    return out, keep_audio


SCREEN_SYSTEM = ("You choose REAL video footage for a devotional Instagram Reel for Indians: a short video with the day's god or festival written on top. Good footage is: an aarti, "
                 "a puja, diyas / lamps / incense, a temple or ghat, the idol (murti) of the wanted god shown respectfully, or a real scene of the wanted festival (procession, "
                 "rangoli, decorations, lights, rituals). "
                 "Open every image with the Read tool. Reject: not Indian Hindu (Buddhist, church, mosque, Western), CGI / cartoon / illustration, a person posing for the camera, "
                 "tourists / selfies, shops or markets, cremation / death scenes, anything disrespectful, dark / blurry / grey footage, footage with burnt-in text, "
                 "watermarks or logos (the temple's own small logo is fine), and an idol or shrine of a DIFFERENT god than the wanted one. A video that shows aarti plates, diyas, "
                 "flames, bells, a temple, a ghat or a generic shrine with no clear god is fine for any god. The text of the Reel covers the lower 40 % of the screen, so the "
                 "subject must be in the upper 60 %. Phones show the video 9:16.")
SCREEN_SCHEMA = {"type": "object", "properties": {"clips": {"type": "array", "items": {"type": "object", "properties": {
    "n": {"type": "integer"}, "fit": {"type": "integer", "description": "1-10: how good this footage is for the Reel"},
    "ok": {"type": "boolean"}, "why": {"type": "string"}}, "required": ["n", "fit", "ok", "why"], "additionalProperties": False}}},
    "required": ["clips"], "additionalProperties": False}


def _screen(cands: list[dict], wanted: str, folder: str, frames_of: dict[int, list[str]], min_fit: int = 7) -> list[tuple[int, int]]:
    """The AI looks at the pictures of each candidate (file names in frames_of) -> [(n, fit)] of the OK ones, best first."""
    listing = "\n".join(f"Clip {n}: {', '.join(frames_of[n])} (tags: {c['tags'][:80]})" for n, c in enumerate(cands, 1) if frames_of.get(n))
    if not listing:
        return []
    res = fun.claude_json(SCREEN_SYSTEM, f"Wanted: {wanted}\n\n{listing}\n\nScore every clip.", SCREEN_SCHEMA, CONFIG.get("membership_model", "sonnet"),
                          purpose="aarti footage screen", folder=folder)["clips"]
    good = []
    for c in res:
        print(f"    clip {c['n']}: fit {c['fit']}/10, {'OK' if c['ok'] else 'rejected'} ({c['why'][:80]})")
        if c["ok"] and c["fit"] >= min_fit:
            good.append((c["n"], c["fit"]))
    return sorted(good, key=lambda x: -x[1])


def _try_clip(c: dict, src: str, wanted: str, folder: str) -> dict | None:
    """Cut the 20 s 9:16 segment, let the AI check the real crop; the result dict or None."""
    try:
        seg, keep_audio = make_segment(src, os.path.join(folder, f"{c['id']}_seg.mp4"), c["source"])
    except Exception as e:
        print(f"  ! {c['id']}: {str(e)[:100]}")
        return None
    crops = []
    for k, at in enumerate((2, 8, 14), 1):
        f = os.path.join(folder, f"{c['id']}_crop{k}.jpg")
        _run([_ffmpeg(), "-v", "error", "-y", "-ss", str(at), "-i", seg, "-frames:v", "1", "-vf", "scale=405:-2", f])
        if os.path.exists(f):
            crops.append(os.path.basename(f))
    if not crops:
        return None
    try:
        ok = _screen([c], wanted, folder, {1: crops}, min_fit=5 if c.get("vetted") else 7)
    except Exception as e:
        if type(e).__name__ == "ClaudeLimit":
            raise
        print(f"  ! crop check failed: {str(e)[:80]}")
        return None
    if not ok:
        return None
    official = c["source"] == "x"
    if official:
        credit = f"Video + sound: {c['temple']} (@{c['handle']}) on X"
    elif c["source"] == "commons":
        credit = f"Video: {c['user'][:40]} / Wikimedia Commons ({c.get('lic', 'CC BY')})"   # CC BY needs the author's name
    else:
        credit = f"Video: {c['user']} / Pixabay"
    print(f"  aarti footage: {c['id']} ({'official ' + c['temple'] if official else ('vetted ' if c.get('vetted') else '') + 'Pixabay ' + c['tags'][:50]}), "
          f"{'own sound' if keep_audio else 'silent'}")
    return {"seg": seg, "id": c["id"], "source": c["source"], "credit": credit, "caption_credit": credit + " 🙏", "silent": not keep_audio,
            "temple": c.get("temple") or "", "handle": c.get("handle") or ""}


def _pixabay_url_by_id(pid: str) -> str:
    """A fresh MP4 link for a Pixabay video id (when a stored link no longer works)."""
    key = os.environ.get("PIXABAY_API_KEY", "")
    if not key:
        return ""
    try:
        hits = requests.get("https://pixabay.com/api/videos/", timeout=25, params={"key": key, "id": pid}).json().get("hits", [])
        v = hits[0].get("videos", {}) if hits else {}
        return (v.get("large") or v.get("medium") or {}).get("url", "")
    except Exception:
        return ""



# 📷 Real photos from Wikimedia Commons for a god / temple / festival that has no video (owner, 12 Nov 2026): CC0, public domain and plain CC BY only (BY-SA would
# bind the Reel), the AI picks sharp, relevant ones by looking at the real 9:16 crops, a slow zoom turns 5 of them into the 20 s background.
PHOTO_QUERIES = {
    "shiva": ["Kashi Vishwanath Temple", "Shiva temple India", "Mahashivratri", "Somnath temple", "Shivling puja"],
    "hanuman": ["Hanuman temple India", "Sankat Mochan temple", "Hanuman statue", "Hanuman Jayanti"],
    "ganesh": ["Siddhivinayak temple", "Lalbaugcha Raja", "Dagdusheth Ganpati", "Ganesh Chaturthi", "Ganesha idol"],
    "vishnu": ["Krishna temple India", "Tirupati Balaji temple", "Jagannath temple Puri", "Janmashtami", "Dahi Handi"],
    "krishna": ["Krishna temple India", "Janmashtami", "Dahi Handi", "Vrindavan temple"],
    "rama": ["Ram Mandir Ayodhya", "Ram Navami", "Ram temple India"],
    "lakshmi": ["Mahalakshmi temple", "Lakshmi puja", "Diwali Lakshmi idol", "Dhanteras"],
    "durga": ["Vaishno Devi", "Durga Puja pandal", "Navratri Garba", "Durga idol", "Kali Puja"],
    "shani": ["Shani Shingnapur", "Shani Dev temple", "Shani idol"],
    "surya": ["Konark Sun Temple", "Surya temple India", "Chhath Puja", "Makar Sankranti"],
}
FESTIVAL_WORDS = {"chhat": "Chhath Puja", "deepavali": "Diwali", "diwali": "Diwali", "dussehra": "Dussehra Ravana", "dasara": "Dussehra Ravana", "navratri": "Navratri Garba",
                  "durga": "Durga Puja pandal", "bhai duj": "Bhai Dooj", "bhai dooj": "Bhai Dooj", "makar sankranti": "Makar Sankranti", "pongal": "Pongal", "ugadi": "Ugadi",
                  "gudi padwa": "Gudi Padwa", "baisakhi": "Baisakhi", "vaisakhi": "Baisakhi", "raksha bandhan": "Raksha Bandhan rakhi", "janmashtami": "Janmashtami", "onam": "Onam Pookalam",
                  "karwa": "Karva Chauth", "karva": "Karva Chauth", "dhanteras": "Dhanteras", "govardhan": "Govardhan Puja", "ram navami": "Ram Navami", "hanuman jayanti": "Hanuman Jayanti",
                  "lohri": "Lohri", "holi": "Holi festival", "guru purnima": "Guru Purnima", "rath yatra": "Rath Yatra Puri", "tulsi": "Tulsi Vivah", "kali puja": "Kali Puja",
                  "ganesh": "Ganesh Chaturthi", "shivratri": "Mahashivratri", "shivaratri": "Mahashivratri", "bihu": "Bihu", "vishu": "Vishu", "teej": "Teej festival"}
LIC_OK = re.compile(r"(?i)^(cc0|public domain|pd\b|cc by( |-)?\d)")
WIKI_HEADERS = {"User-Agent": "KhabarBawaalStudio/1.0 (github.com/vikashlal12345)"}


IDOL_QUERIES = {
    "shiva": ["shiva idol", "lord shiva statue", "shivling temple", "shiva murti"],
    "hanuman": ["hanuman idol", "hanuman statue", "hanuman murti", "lord hanuman temple"],
    "ganesh": ["ganesh idol", "ganesha statue", "ganpati murti", "lord ganesha"],
    "vishnu": ["krishna idol", "vishnu idol", "radha krishna murti", "krishna statue", "jagannath idol"],
    "krishna": ["krishna idol", "radha krishna murti", "krishna statue"],
    "rama": ["ram darbar idol", "lord rama statue", "ram sita hanuman idol"],
    "lakshmi": ["lakshmi idol", "goddess lakshmi statue", "maa lakshmi murti", "lakshmi puja idol"],
    "durga": ["durga idol", "maa durga murti", "goddess durga statue", "durga puja idol"],
    "shani": ["shani dev idol", "shani statue", "shani temple idol", "shani dev murti"],
    "surya": ["surya dev idol", "sun god statue", "surya temple", "sun temple konark"],
}


def idol_queries(plan: dict, subj: dict) -> list[str]:
    """Pixabay photo searches: the idol of the day's god (weekday / Navratri) or the festival's own scene, e.g. 'lakshmi idol'."""
    qs = []
    if subj.get("kind") != "weekday":
        text = " ".join(subj.get("festivals", []) + [subj.get("name", "")]).lower()
        for word, q in FESTIVAL_WORDS.items():
            if word in text and q.split()[0].lower() not in [x.split()[0].lower() for x in qs]:
                qs.append(q)
    key = _family_key(plan, subj)
    qs += [q for q in IDOL_QUERIES.get(key, []) if q not in qs]
    return qs[:6]


def pixabay_photos(queries: list[str], used: set[str], want: int = 24) -> list[dict]:
    """Real high-quality PHOTOS from Pixabay (free licence, no credit needed): vertical first (they fill the 9:16 screen), the most liked first, AI / CGI / illustration
    tagged ones left out: [{title, thumb, w, h, lic, artist, q, page, source}]."""
    key = os.environ.get("PIXABAY_API_KEY", "")
    if not key:
        return []
    out, seen = [], set()
    for orient in ("vertical", "all"):
        for q in queries:
            try:
                hits = requests.get("https://pixabay.com/api/", timeout=25, params={"key": key, "q": q, "image_type": "photo", "orientation": orient, "safesearch": "true",
                                                                               "per_page": 30, "min_width": 1000, "min_height": 1200, "order": "popular"}).json().get("hits", [])
            except Exception as e:
                print(f"  ! Pixabay photos '{q}': {str(e)[:60]}")
                continue
            for h in hits:
                tid = f"pxp{h['id']}"
                tags = h.get("tags", "")
                if tid in seen or tid in used or FAKE.search(tags) or re.search(r"(?i)\b(ai|generated|midjourney|render|3d|graphic|vector)\b", tags) or not h.get("largeImageURL"):
                    continue
                seen.add(tid)
                out.append({"title": tid, "thumb": h["largeImageURL"], "w": h.get("imageWidth", 0), "h": h.get("imageHeight", 0), "lic": "Pixabay", "artist": h.get("user", "Pixabay contributor"),
                            "q": q, "page": h.get("pageURL", ""), "source": "pixabay-photos", "likes": h.get("likes", 0), "tags": tags})
        if len(out) >= 12:
            break
    out.sort(key=lambda c: (-(c["h"] > c["w"]), -c["likes"]))
    return out[:want]


def photo_queries(plan: dict, subj: dict) -> list[str]:
    qs = []
    if subj.get("kind") != "weekday":
        text = " ".join(subj.get("festivals", []) + [subj.get("name", "")]).lower()
        for word, q in FESTIVAL_WORDS.items():
            if word in text and q not in qs:
                qs.append(q)
        if not qs:
            qs.append(re.split(r"[(/:]", (subj.get("festivals") or [subj.get("name", "")])[0])[0].strip())
    key = _family_key(plan, subj)
    for q in PHOTO_QUERIES.get(key, []):
        if q not in qs:
            qs.append(q)
    return qs[:8]


def _wiki_json(params: dict) -> dict:
    import time
    for k in range(4):
        try:
            r = requests.get("https://commons.wikimedia.org/w/api.php", params=params, headers=WIKI_HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(3 * (k + 1))
    return {}


def commons_photos(queries: list[str], used: set[str], per_query: int = 14) -> list[dict]:
    """Free-licence photos (>= 1600 px) for these searches, thumbnails 1280 px wide: [{title, thumb, w, h, lic, artist, page}]."""
    titles = []
    for q in queries:
        j = _wiki_json({"action": "query", "format": "json", "list": "search", "srnamespace": 6, "srlimit": 30, "srsearch": "filetype:bitmap " + q})
        found = [x["title"] for x in j.get("query", {}).get("search", [])]
        titles += [(t, q) for t in found if t not in used and t not in [x[0] for x in titles]][:per_query + 14]
    out = []
    for i in range(0, len(titles), 40):
        chunk = titles[i:i + 40]
        j = _wiki_json({"action": "query", "format": "json", "titles": "|".join(t for t, _ in chunk), "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
                        "iiurlwidth": 1280, "iiextmetadatafilter": "LicenseShortName|Artist"})
        qmap = dict(chunk)
        for p in j.get("query", {}).get("pages", {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            md = ii.get("extmetadata", {})
            lic = md.get("LicenseShortName", {}).get("value", "")
            if not LIC_OK.match(lic or "") or re.search(r"(?i)\bsa\b|share", lic) or (ii.get("width") or 0) < 1600 or not ii.get("thumburl") or "image" not in (ii.get("mime") or ""):
                continue
            out.append({"title": p["title"], "thumb": ii["thumburl"], "w": ii["width"], "h": ii["height"], "lic": lic, "q": qmap.get(p["title"], ""),
                        "artist": re.sub(r"<[^>]+>", "", md.get("Artist", {}).get("value", "")).strip()[:50] or "Wikimedia Commons contributor",
                        "page": "https://commons.wikimedia.org/wiki/" + p["title"].replace(" ", "_")})
    # keep the order of the searches (most specific first), a few per search
    order = {q: i for i, q in enumerate(queries)}
    out.sort(key=lambda c: order.get(c["q"], 99))
    per, res = {}, []
    for c in out:
        per[c["q"]] = per.get(c["q"], 0) + 1
        if per[c["q"]] <= per_query:
            res.append(c)
    return res[:36]


def _cover_crop(im, w: int = 1080, h: int = 1920, zoom: float = 1.0):
    """Centre crop of a photo to 9:16 (cover), `zoom` 1.0 = the whole crop."""
    from PIL import Image
    k = max(w / im.width, h / im.height) * zoom
    big = im.resize((int(im.width * k) + 1, int(im.height * k) + 1), Image.BILINEAR)
    x0, y0 = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((x0, y0, x0 + w, y0 + h))


def photo_segment(images: list, out: str, secs: float = SEG_SECS) -> str:
    """3-5 photos -> a silent 1080x1920 H.264 video: each with a slow zoom-in, 0.6 s cross-fades (frames go to JPEG files, ONE ffmpeg run)."""
    import shutil
    from PIL import Image
    n = len(images)
    per = (secs + 0.6 * (n - 1)) / n
    fps = 30
    folder = tempfile.mkdtemp(prefix="slides_", dir=os.path.dirname(out))
    step = per - 0.6   # photo i starts at i * step and lasts `per` s; the first 0.6 s of photo i cross-fades from photo i-1
    total = int(secs * fps)

    def frame_of(i: int, t_local: float) -> "Image.Image":
        z = 1.0 + 0.12 * min(1.0, max(0.0, t_local / per))
        return _cover_crop(images[i], zoom=z)
    for f in range(total):
        t = f / fps
        i = min(n - 1, int(t // step))
        t_local = t - i * step
        if i > 0 and t_local < 0.6:
            fr = Image.blend(frame_of(i - 1, t_local + step), frame_of(i, t_local), t_local / 0.6)
        else:
            fr = frame_of(i, t_local)
        fr.save(os.path.join(folder, f"{f:05d}.jpg"), "JPEG", quality=94)
    r = _run([_ffmpeg(), "-nostdin", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", os.path.join(folder, "%05d.jpg"), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
              "-t", str(secs), "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "17", "-preset", "fast", "-vf", "format=yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", out])
    shutil.rmtree(folder, ignore_errors=True)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"slideshow failed: {r.stderr[:200]}")
    return out


PHOTO_SYSTEM = ("You choose REAL photos for a devotional Instagram Reel for Indians: a slow slideshow (one photo at a time, full screen 9:16, zoom-in) behind the day's god / temple / "
                "festival text. THE OWNER WANTS VERY GOOD QUALITY: only photos a devotional page would be proud to show. For a god's day the main subject must be the IDOL (murti) or statue of THAT "
                "god, clearly visible, sharp, well lit, fills much of the picture (not a distant temple, a tower or a building); for a festival, the idol or a clear festival scene. Reject: temple exteriors "
                "and towers, anything with construction, scaffolding, workers, wires, vehicles or signboards in view, cluttered or dark pictures, low resolution, heavy filters, watermarks, and an idol "
                "of a different god.  Open the picture with the Read tool: a grid of numbered 9:16 crops, exactly what the phone will show. GOOD: a real, sharp, well lit photo that clearly shows the "
                "wanted temple, idol, ritual or festival scene, with the main subject inside the crop, respectful, no text overlay or watermark. Reject: blurry, dark, tiny subject, "
                "a wrong place or god, tourists posing or selfies, close-ups of strangers' faces, a diagram, a map, a painting of something else, a modern building with no devotional "
                "element, non-Hindu scenes, photos taken in a museum or outside India, photos where strangers' faces are the main subject, and anything disrespectful. The lower 40 % of the screen is covered by the Reel's text, so the subject must be in the upper 60 %.")
PHOTO_SCHEMA = {"type": "object", "properties": {"photos": {"type": "array", "items": {"type": "object", "properties": {
    "n": {"type": "integer"}, "ok": {"type": "boolean"}, "fit": {"type": "integer", "description": "1-10"}, "why": {"type": "string"}},
    "required": ["n", "ok", "fit", "why"], "additionalProperties": False}}}, "required": ["photos"], "additionalProperties": False}


def photo_slideshow(plan: dict, subj: dict, state: dict, tmp: str, wanted: str) -> dict | None:
    """The real-photo slideshow for today's god / festival, or None."""
    from PIL import Image, ImageDraw
    import io
    import reel
    queries = photo_queries(plan, subj)
    iq = idol_queries(plan, subj)
    if not queries and not iq:
        return None
    used = set(state.get("aarti_photos_used", []))
    cands = pixabay_photos(iq, used) + commons_photos(queries, used, per_query=6)[:12]
    print(f"  aarti photos: {len(cands)} candidates ({sum(1 for c in cands if c.get('source') == 'pixabay-photos')} Pixabay idol photos) for {iq + queries}")
    if len(cands) < 3:
        return None
    folder = tempfile.mkdtemp(prefix="photos_", dir=tmp)
    kept = []
    for c in cands:
        if c.get("source") == "pixabay-photos":
            try:
                r = requests.get(c["thumb"], headers=UA, timeout=40)
                r = r if r.status_code == 200 else None
            except Exception:
                r = None
        else:
            r = reel.wiki_get(c["thumb"])
        if r is None:
            continue
        try:
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            continue
        if min(im.size) < 900 or max(im.size) < 1400:   # very good quality only: too small a photo would be soft on the full screen
            continue
        c["img"] = im
        kept.append(c)
    if len(kept) < 3:
        return None
    # AI check on contact sheets of the real 9:16 crops (12 per sheet)
    good = []
    kept = kept[:30]
    for b in range(0, len(kept), 12):
        batch = kept[b:b + 12]
        tw, th = 270, 480
        sheet = Image.new("RGB", (6 * tw, 2 * th), "black")
        for i, c in enumerate(batch):
            crop = _cover_crop(c["img"], tw, th)
            d = ImageDraw.Draw(crop)
            d.rectangle((0, 0, 44, 30), fill="black")
            d.text((6, 6), str(i + 1), fill="yellow")
            sheet.paste(crop, ((i % 6) * tw, (i // 6) * th))
        name = f"sheet_{b // 12}.jpg"
        sheet.save(os.path.join(folder, name), "JPEG", quality=88)
        try:
            res = fun.claude_json(PHOTO_SYSTEM, f"Wanted: {wanted}. File: {name}. Score every numbered crop.\n" + "\n".join(f"{i}: {c.get('tags') or c['title'][5:80]}" for i, c in enumerate(batch, 1)),
                                  PHOTO_SCHEMA, CONFIG.get("membership_model", "sonnet"), purpose="aarti photo screen", folder=folder)["photos"]
        except Exception as e:
            if type(e).__name__ == "ClaudeLimit":
                raise
            print(f"  ! photo screen failed: {str(e)[:80]}")
            continue
        for p in res:
            if 1 <= p["n"] <= len(batch) and p["ok"] and p["fit"] >= 7:
                good.append((p["fit"], batch[p["n"] - 1]))
                print(f"    photo: fit {p['fit']}/10 {batch[p['n'] - 1]['title'][5:60]} ({p['why'][:50]})")
    good.sort(key=lambda x: -x[0])
    pick, seen_q = [], {}
    for fit, c in good:   # at most 3 from one search so the slideshow shows variety
        if seen_q.get(c["q"], 0) >= 3:
            continue
        seen_q[c["q"]] = seen_q.get(c["q"], 0) + 1
        pick.append(c)
        if len(pick) == 5:
            break
    if len(pick) < 3:
        print(f"  aarti photos: only {len(pick)} good photos")
        return None
    out = photo_segment([c["img"] for c in pick], os.path.join(folder, "slides.mp4"))
    srcs = {c.get("source", "commons") for c in pick}
    credit = "Photos: Pixabay" if srcs == {"pixabay-photos"} else "Photos: Wikimedia Commons (CC BY / CC0)" if "pixabay-photos" not in srcs else "Photos: Pixabay, Wikimedia Commons"
    names = "; ".join(dict.fromkeys(f"{c['artist']} ({c['lic']})" for c in pick))
    state["aarti_photos_used"] = (state.get("aarti_photos_used", []) + [c["title"] for c in pick])[-80:]
    print(f"  aarti footage: slideshow of {len(pick)} real photos from Wikimedia Commons")
    return {"seg": out, "id": "ph" + str(abs(hash(pick[0]["title"])))[:8], "source": "commons-photos", "credit": credit, "caption_credit": f"Photos: {names[:260]} 🙏",
            "silent": True, "temple": "", "handle": "", "pages": [c["page"] for c in pick]}


def find_footage(plan: dict, subj: dict, today: str, state: dict, tmp: str) -> dict | None:
    """The real aarti footage for today's god / festival, or None (the caller then uses the AI-picture flow). Order: the god's official temple account
    (fresh, own sound), the vetted LINK list (downloaded now), a fresh Pixabay search (AI-screened)."""
    used_order = list(state.get("aarti_used", []))
    used = set(used_order)
    wanted = (f"{plan.get('deity') or subj.get('deity') or subj.get('name')} ({subj.get('name', '')}"
              f"{'; festival: ' + ', '.join(subj['festivals']) if subj.get('festivals') else ''})")
    folder = tempfile.mkdtemp(prefix="aarti_", dir=tmp)
    # 1) official temple video (screened like any new clip)
    cands = official_candidates(plan, subj, used)
    for n, c in enumerate(cands, 1):
        path = os.path.join(folder, f"{c['id']}.mp4")
        if _download(c["url"], path):
            got = _try_clip(c, path, wanted, folder)
            if got:
                return got
    # 2) the vetted link list: the clip is downloaded from its link right now. The festival's / god's OWN clips first, then real photos of that
    #    temple / festival (owner 12 Nov 2026), then the generic clips (diyas, ghat, temple aerial)
    vet = vetted_candidates(plan, subj, used_order)
    print(f"  aarti footage: {len(cands)} official, {len(vet)} vetted links for {wanted}")

    def try_vetted(group: list[dict]) -> dict | None:
        for c in group[:6]:
            path = os.path.join(folder, f"{c['id']}.mp4")
            if not _download(c["url"], path):
                fresh = _pixabay_url_by_id(c["pid"]) if c["source"] == "pixabay" else ""
                if not fresh or not _download(fresh, path):
                    print(f"  ! vetted clip {c['id']}: link dead")
                    continue
            got = _try_clip(c, path, wanted, folder)
            if got:
                return got
        return None
    got = try_vetted([c for c in vet if c.get("specific")])
    if got:
        return got
    if os.environ.get("AARTI_PHOTOS", "1") != "0":
        try:
            got = photo_slideshow(plan, subj, state, tmp, wanted)
        except Exception as e:
            if type(e).__name__ == "ClaudeLimit":
                raise
            print(f"  ! photo slideshow failed: {type(e).__name__}: {str(e)[:100]}")
            got = None
        if got:
            return got
    got = try_vetted([c for c in vet if not c.get("specific")])
    if got:
        return got
    # 3) last resort: search Pixabay now and let the AI pick from thumbnails
    pix = pixabay_candidates(queries_for(plan, subj), used)[:14]
    print(f"  aarti footage: searching Pixabay: {len(pix)} candidates")
    frames, keep = {}, []
    for n, c in enumerate(pix, 1):
        p = os.path.join(folder, f"t{n}.jpg")
        if _download(c["thumb"], p, 5):
            frames[len(keep) + 1] = [f"t{n}.jpg"]
            keep.append(c)
    if not keep:
        return None
    try:
        ranked = _screen(keep, wanted, folder, frames)
    except Exception as e:
        if type(e).__name__ == "ClaudeLimit":
            raise
        print(f"  ! footage screen failed: {str(e)[:80]}")
        return None
    for i, fit in ranked[:3]:
        c = keep[i - 1]
        src = os.path.join(folder, f"{c['id']}.mp4")
        if _download(c["url"], src):
            got = _try_clip(c, src, wanted, folder)
            if got:
                return got
    return None
