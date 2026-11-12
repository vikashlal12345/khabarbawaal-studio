"""🪔 Mac-only collector (owner, 12 Nov 2026: 'I want the same Somnath-like Reel'): X shows @Somnath_Temple's videos to this Mac but not to GitHub's computers, so
this Mac fetches the newest aarti / darshan videos, cuts a 20 s vertical piece (the loudest stretch, with the temple's own sound) and uploads it to the public GitHub
release 'aarti-bank'. The 6 AM robot (aarti.release_candidates) downloads it from there, no X needed. Called hourly from mac_stats.py (only while the Mac is awake;
at most once every 3 hours); the release holds at most 2 pieces, each deleted by the robot right after its Reel is saved, leftovers after 3 days. Run by hand:  python3 aarti_collect.py
Credit on the Reel: 'Video + sound: Shree Somnath Temple (@Somnath_Temple) on X'."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HANDLES = [("Somnath_Temple", "Shree Somnath Temple")]
TAG = "aarti-bank"
REPO = "vikashlal12345/khabarbawaal-studio"
STAMP = Path.home() / ".khabarbawaal-aarti-last"


def gh(*args, check=True):
    exe = shutil.which("gh") or next((p for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh") if os.path.exists(p)), "gh")
    return subprocess.run([exe, *args, "-R", REPO] if args and args[0] in ("release",) else [exe, *args], capture_output=True, text=True, check=check)


def assets() -> dict[str, str]:
    """name -> created_at of the release's files ({} when the release does not exist yet)."""
    r = gh("release", "view", TAG, "--json", "assets", check=False)
    if r.returncode != 0:
        gh("release", "create", TAG, "--title", "Aarti clips", "--notes", "20 s pieces of official temple aarti videos for the 6 AM God Reel (aarti_collect.py, Mac). Credit: the temple.", check=False)
        return {}
    return {a["name"]: a.get("createdAt", "") for a in json.loads(r.stdout).get("assets", [])}


def run(force: bool = False) -> int:
    if not force and STAMP.exists() and time.time() - STAMP.stat().st_mtime < 3 * 3600:
        return 0
    sys_path = str(Path(__file__).resolve().parent)
    import sys
    sys.path.insert(0, sys_path)
    import aarti
    import reel
    have = assets()
    added = 0
    handles = tuple(h for h, _ in HANDLES)
    names = dict(HANDLES)
    try:
        vids = reel.official_videos(set(), handles, hours=96, max_wait=60)
    except Exception as e:
        print(f"aarti collect: X not readable now ({str(e)[:80]})")
        return 0
    fresh = [n for n in have if n.startswith("somnath-")]
    for v in vids[:4]:
        if len(fresh) + added >= 2:   # the public folder holds at most 2 pieces
            break
        name = f"somnath-{v['at']:%Y%m%d}-{v['id']}.mp4"
        if name in have or v["secs"] < 12 or v["secs"] > 150:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.mp4")
            if not aarti._download(v["mp4"], src, 400):
                print(f"aarti collect: download failed for {v['id']}")
                continue
            try:
                seg, audio = aarti.make_segment(src, os.path.join(tmp, name), "x")
            except Exception as e:
                print(f"aarti collect: cut failed for {v['id']}: {str(e)[:80]}")
                continue
            r = gh("release", "upload", TAG, seg, "--clobber", check=False)
            if r.returncode == 0:
                added += 1
                print(f"aarti collect: uploaded {name} ({os.path.getsize(seg) // 1024} KB, own sound: {audio})")
            else:
                print(f"aarti collect: upload failed: {r.stderr[:120]}")
    # a piece stays at most 3 days (the robot deletes a piece right after its Reel is saved)
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    for name in list(have):
        m = re.match(r"somnath-(\d{8})-", name)
        if m and datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=timezone.utc) < cutoff:
            gh("release", "delete-asset", TAG, name, "-y", check=False)
            print(f"aarti collect: removed old {name}")
    STAMP.touch()
    return added


if __name__ == "__main__":
    print("added:", run(force=True))
