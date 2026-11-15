"""📊 Views from the owner's screen recording of Instagram's Content list (owner's choice, 14 Oct 2026: no Meta API).

    python3 tools/views_from_recording.py ~/Downloads/ScreenRecording_....MP4 [--save]

Mac only: pulls a frame every 1.5 s (imageio-ffmpeg), reads the text with Apple's Vision (tools/ocr.swift, compiled
once to tools/.ocr), and prints every post with its type, IST time and views, then averages for Reels vs photos.
The Content screen shows times in US Pacific time (PDT): +12:30 h = IST. A lone "0" is sometimes not read: those
rows are counted as 0 views (checked by eye on 14 Oct 2026); the newest post may simply be too new.
"""
import collections
import re
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent


def main(video: str) -> None:
    import imageio_ffmpeg
    ocr = HERE / ".ocr"
    if not ocr.exists():
        subprocess.run(["swiftc", "-O", str(HERE / "ocr.swift"), "-o", str(ocr)], check=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-i", video, "-vf", "fps=1/1.5",
                        "-q:v", "2", f"{tmp}/f_%03d.jpg"], check=True)
        out = subprocess.run([str(ocr)] + sorted(str(p) for p in Path(tmp).glob("f_*.jpg")),
                             capture_output=True, text=True).stdout
    frames = collections.defaultdict(list)
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) == 4:
            frames[p[0]].append((float(p[1]), float(p[2]), p[3].strip()))
    meta_re = re.compile(r"(Reel|Photo).*?((Today|Yesterday|\d{1,2} \w{3}) at (\d{1,2}):(\d{2}))")
    num_re = re.compile(r"^([\d,]+|[Oo])$")
    today = datetime.now()
    rows = {}
    for f in sorted(frames):
        items = frames[f]
        for my, _, mt in [(y, x, t) for y, x, t in items if 0.17 < x < 0.26 and meta_re.search(t) and y > 0.22]:
            m = meta_re.search(mt)
            titles = [(y, t) for y, x, t in items if 0.17 < x < 0.26 and my - 0.045 < y < my - 0.01 and not meta_re.search(t)]
            if not titles or max(titles)[1].lower().startswith("video story"):
                continue
            ty, title = max(titles)
            nums = [t for y, x, t in items if x > 0.83 and num_re.match(t) and ty - 0.01 < y < my + 0.015]
            v = None if not nums else (0 if nums[0] in "Oo" else int(nums[0].replace(",", "")))
            day = m.group(3)
            date = (today if day == "Today" else today - timedelta(days=1) if day == "Yesterday"
                    else datetime.strptime(f"{day} {today.year}", "%d %b %Y"))
            ist = date.replace(hour=int(m.group(4)), minute=int(m.group(5)), second=0, microsecond=0) + timedelta(hours=12, minutes=30)
            r = rows.setdefault((m.group(1), ist), {"kind": m.group(1), "ist": ist, "title": title, "views": None})
            if v is not None and (r["views"] is None or v > r["views"]):
                r["views"] = v
    posts = sorted(rows.values(), key=lambda r: r["ist"]) or dashboard_rows(frames, video)
    for r in posts:
        r["views"] = r["views"] or 0
        print(f"{r['ist']:%d %b %H:%M} IST  {r['kind']:5}  {r['views']:>6}  {r['title'][:70]}")
    for kind in ("Reel", "Photo"):
        vs = [r["views"] for r in posts if r["kind"] == kind]
        if vs:
            print(f"{kind}s: {len(vs)} posts, avg {sum(vs) / len(vs):.0f}, median {statistics.median(vs)}")
    if "--save" in sys.argv:
        save_viral(posts)


def dashboard_rows(frames: dict, video: str) -> list[dict]:
    """The other screen (Professional dashboard -> All content, owner's recording of 16 Oct 2026): each row is
    'title... 21h' (age instead of a date) with the views on the right. Posting time = recording time - age (the
    recording time comes from the file name 'ScreenRecording_MM-DD-YYYY HH-MM-SS', else now). Reels only (stories
    have no title)."""
    m = re.search(r"(\d{2})-(\d{2})-(\d{4}) (\d{2})-(\d{2})-(\d{2})", Path(video).name)
    rec = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)), int(m.group(4)), int(m.group(5))) if m else datetime.now()
    age_re = re.compile(r"^(.*?)\s*(\d{1,2})([mhd])$")
    num_re = re.compile(r"^(\d[\d,.]*)(K?)$")
    rows = {}
    for f in sorted(frames):
        items = frames[f]
        for y, x, t in items:
            g = age_re.match(t) if 0.15 < x < 0.21 else None
            if not g or len(g.group(1)) < 6:
                continue
            nums = [(abs(ny - y), nt) for ny, nx, nt in items if nx > 0.8 and num_re.match(nt) and abs(ny - y) < 0.02]
            if not nums:
                continue
            n = num_re.match(min(nums)[1])
            views = int(float(n.group(1).replace(",", "")) * (1000 if n.group(2) else 1))
            age = timedelta(minutes=int(g.group(2)) * {"m": 1, "h": 60, "d": 1440}[g.group(3)])
            title = g.group(1).rstrip(". …")
            r = rows.setdefault((title, g.group(2) + g.group(3)), {"kind": "Reel", "ist": rec - age, "title": title, "views": 0})
            r["views"] = max(r["views"], views)
    return sorted(rows.values(), key=lambda r: r["ist"])


def save_viral(posts: list[dict]) -> None:
    """📊 The owner's exact view numbers (owner, 15 Oct 2026): Reels older than 20 h (title = the caption's first
    line) go to state/viral.json 'recorded', merged with earlier recordings (highest views kept, last 30 days); every
    Reel picker and writer reads it next to the daily embed ranking (reel.viral_memory)."""
    import json
    path = HERE.parent / "state" / "viral.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    seen = {r["title"]: r for r in old.get("recorded", [])}
    for r in posts:
        if r["kind"] != "Reel" or datetime.now() - r["ist"] < timedelta(hours=12):
            continue
        prev = seen.get(r["title"], {})
        seen[r["title"]] = {"title": r["title"], "views": max(r["views"], prev.get("views", 0)),
                            "at": r["ist"].strftime("%Y-%m-%d %H:%M"), "note": prev.get("note", "")}
    keep = [r for r in seen.values() if datetime.now() - datetime.strptime(r["at"], "%Y-%m-%d %H:%M") < timedelta(days=30)]
    keep.sort(key=lambda r: -r["views"])
    old["recorded"] = keep   # exact 'Views' from the app; mac_stats.py fills 'top'/'weak' from embed views daily
    path.write_text(json.dumps(old, ensure_ascii=False, indent=1))
    print(f"Saved {len(keep)} recorded Reels (top {keep[0]['views'] if keep else 0} views) to {path} (upload with save.py)")


if __name__ == "__main__":
    main(sys.argv[1])
