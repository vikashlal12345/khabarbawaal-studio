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
                 ("ganesh|ganpati|vinayaka", ["ganesh"]), ("janmashtami|krishna|ram navami|rama|vishnu", ["vishnu"]), ("hanuman", ["hanuman"]),
                 ("shivratri|shivaratri|sawan|shravan|shiv", ["shiva"]), ("chhath|surya|sankranti|pongal", ["surya"])]
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
    if key:
        wanted.append({"krishna": "vishnu", "rama": "vishnu"}.get(key, key))
    fest = " ".join(subj.get("festivals", []) + [subj.get("name", "")]).lower()
    if subj.get("kind") != "weekday":
        for pattern, gods in FESTIVAL_GODS:
            if re.search(pattern, fest):
                wanted += [g_ for g_ in gods if g_ not in wanted]
    last_use = {cid: i for i, cid in enumerate(used_order)}
    out = []
    for c in clips:
        gods = c.get("gods", [])
        rank = next((i for i, w in enumerate(wanted) if w in gods), None)
        if rank is None and "generic" in gods:
            rank = len(wanted)
        if rank is None:
            continue
        out.append((rank, last_use.get(c["id"], -1), c))
    out.sort(key=lambda x: (x[0], x[1]))
    return [{"id": c["id"], "source": "pixabay", "url": c["url"], "w": c.get("w", 0), "h": c.get("h", 0), "dur": c.get("dur", 0), "tags": c.get("tags", ""),
             "user": c.get("user", ""), "thumb": "", "q": "vetted", "vetted": True, "pid": c["id"][2:]} for _, _, c in out]


def official_candidates(plan: dict, subj: dict, used: set[str]) -> list[dict]:
    """The god's official temple account on X (videos of the last 4 days, 10 s +), e.g. @Somnath_Temple for Shiva."""
    import reel
    key = _family_key(plan, subj)
    handles = TEMPLE_X.get(key, [])
    if not handles:
        return []
    names = dict(handles)
    try:
        vids = reel.official_videos(set(), tuple(h for h, _ in handles), hours=96, max_wait=45)
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
    try:
        with requests.get(url, headers=UA, stream=True, timeout=60) as r:
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


SCREEN_SYSTEM = ("You choose REAL video footage for a devotional Instagram Reel for Indians: a short aarti / puja / temple video with the day's god written on top. "
                 "Open every image with the Read tool. Reject: not Indian Hindu (Buddhist, church, mosque, Western), CGI / cartoon / illustration, a person posing for the camera, "
                 "tourists / selfies, shops or markets, cremation / death scenes, anything disrespectful, dark / blurry / grey footage, footage with burnt-in text, "
                 "watermarks or logos (the temple's own small logo is fine), and an idol or shrine of a DIFFERENT god than the wanted one. A video that shows aarti plates, diyas, "
                 "flames, bells, a temple, a ghat or a generic shrine with no clear god is fine for any god. The text of the Reel covers the lower 40 % of the screen, so the "
                 "subject must be in the upper 60 %. Phones show the video 9:16.")
SCREEN_SCHEMA = {"type": "object", "properties": {"clips": {"type": "array", "items": {"type": "object", "properties": {
    "n": {"type": "integer"}, "fit": {"type": "integer", "description": "1-10: how good this footage is for the Reel"},
    "ok": {"type": "boolean"}, "why": {"type": "string"}}, "required": ["n", "fit", "ok", "why"], "additionalProperties": False}}},
    "required": ["clips"], "additionalProperties": False}


def _screen(cands: list[dict], wanted: str, folder: str, frames_of: dict[int, list[str]]) -> list[tuple[int, int]]:
    """The AI looks at the pictures of each candidate (file names in frames_of) -> [(n, fit)] of the OK ones, best first."""
    listing = "\n".join(f"Clip {n}: {', '.join(frames_of[n])} (tags: {c['tags'][:80]})" for n, c in enumerate(cands, 1) if frames_of.get(n))
    if not listing:
        return []
    res = fun.claude_json(SCREEN_SYSTEM, f"Wanted: {wanted}\n\n{listing}\n\nScore every clip.", SCREEN_SCHEMA, CONFIG.get("membership_model", "sonnet"),
                          purpose="aarti footage screen", folder=folder)["clips"]
    good = []
    for c in res:
        print(f"    clip {c['n']}: fit {c['fit']}/10, {'OK' if c['ok'] else 'rejected'} ({c['why'][:80]})")
        if c["ok"] and c["fit"] >= 7:
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
        ok = _screen([c], wanted, folder, {1: crops})
    except Exception as e:
        if type(e).__name__ == "ClaudeLimit":
            raise
        print(f"  ! crop check failed: {str(e)[:80]}")
        return None
    if not ok:
        return None
    official = c["source"] == "x"
    credit = f"Video + sound: {c['temple']} (@{c['handle']}) on X" if official else f"Video: {c['user']} / Pixabay"
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
    # 2) the vetted link list: the clip is downloaded from its link right now
    vet = vetted_candidates(plan, subj, used_order)
    print(f"  aarti footage: {len(cands)} official, {len(vet)} vetted links for {wanted}")
    for c in vet[:4]:
        path = os.path.join(folder, f"{c['id']}.mp4")
        if not _download(c["url"], path):
            fresh = _pixabay_url_by_id(c["pid"])
            if not fresh or not _download(fresh, path):
                print(f"  ! vetted clip {c['id']}: link dead")
                continue
        got = _try_clip(c, path, wanted, folder)
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
