"""CI test of the real-aarti 6 AM Reel (owner approved 12 Nov 2026): makes the Reel for the given dates in a TEMP folder (nothing is saved to the app or the repo)
and copies the MP4s to ./aarti_out so the workflow can attach them. Usage: python tools/aarti_ci_test.py 2026-11-16 2026-11-10"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import generate as g   # noqa: E402
import reel            # noqa: E402
import status          # noqa: E402

tmpdocs = Path(tempfile.mkdtemp(prefix="docs_"))
(tmpdocs / "posts").mkdir()
(tmpdocs / "stats").mkdir()
g.DOCS, g.POSTS_DIR, g.FEED_FILE, g.STATE_FILE = tmpdocs, tmpdocs / "posts", tmpdocs / "feed.json", tmpdocs / "state.json"
for name in ("step", "done", "fail", "info", "start"):
    if hasattr(status, name):
        setattr(status, name, lambda *a, **k: None)   # no messages to the app's Activity box
out = Path("aarti_out")
out.mkdir(exist_ok=True)
for day in sys.argv[1:]:
    reel.SERIES_KIND = "reel_devotion"
    reel.TAGS.clear()
    tmp = tempfile.mkdtemp(prefix="run_")
    feed, state = [], {}
    try:
        reel.make_devotion_reel(state, feed, day, tmp)
    except Exception as e:
        print(f"RESULT {day}: FAILED {type(e).__name__}: {str(e)[:300]}", flush=True)
        continue
    p = feed[0]
    shutil.copy(g.DOCS / p["video"], out / f"{day}.mp4")
    print(f"RESULT {day}: {p['headline'][:90]} | footage={reel.TAGS.get('footage')} | silent={reel.TAGS.get('silent')} | proof={p['proof']['state']} | credit line in caption: "
          f"{[l for l in p['caption'].splitlines() if 'Video' in l][:1]}", flush=True)
print("ALLDONE", flush=True)
