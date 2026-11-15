"""CI test of the two app paths that share today's changed Reel code (owner approved 12 Nov 2026): ➕ Create (news Reel from typed words) and 🔄 Remake.
Temp folder only: nothing is saved to the app or the repo."""
import json
import os
import re
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
os.environ["SUMMARY_FILE"] = str(tmpdocs / "summary.md")
for name in ("step", "done", "fail", "info", "start"):
    if hasattr(status, name):
        setattr(status, name, lambda *a, **k: None)
out = Path("paths_out")
out.mkdir(exist_ok=True)
results = []


def res(name, ok, detail=""):
    results.append({"test": name, "ok": bool(ok), "detail": detail})
    print("RESULT " + json.dumps(results[-1], ensure_ascii=False), flush=True)


# 1) ➕ Create: a news Reel from typed words
import create_post   # noqa: E402
try:
    code = create_post.make_reel("Make a Reel on the latest big Bollywood news")
    feed = g.load_json(g.FEED_FILE, [])
    p = feed[0] if feed else {}
    secs = reel.video_seconds(str(tmpdocs / p["video"])) if p.get("video") else 0
    if p.get("video"):
        shutil.copy(tmpdocs / p["video"], out / "create.mp4")
    res("create: a Reel was made", code == 0 and bool(p.get("video")), f"{p.get('headline', '')[:80]} | {secs:.1f}s | {(p.get('proof') or {}).get('state')}")
    res("create: sensible length (12-28 s)", 12 <= secs <= 28, f"{secs:.1f}")
    cap = p.get("caption", "")
    res("create: caption has <= 5 hashtags, no Devanagari", len(re.findall(r"(?<!\w)#\w+", cap.split("\n\n(")[0])) <= 5 and not re.search(r"[ऀ-ॿ]", cap))
except Exception as e:
    res("create path", False, f"crashed {type(e).__name__}: {str(e)[:200]}")

# 2) 🔄 Remake of a breaking Reel
try:
    feed, state = [], {}
    ok = reel.make("reel_break_night", state, feed, 1, 1)
    g.save_json(g.FEED_FILE, feed)
    g.save_json(g.STATE_FILE, state)
    old = feed[0]
    import remake
    remake.run(old["id"], "Same story. Make the opening hook shorter and punchier.")
    feed2 = g.load_json(g.FEED_FILE, [])
    same = [p for p in feed2 if p["id"] == old["id"]]
    new = same[-1] if same else {}
    res("remake: still exactly one Reel with the old id", len(same) == 1)
    res("remake: new file names (the app caches by path)", bool(new) and new.get("video") != old.get("video") and new.get("image") != old.get("image"), f"{old.get('video')} -> {new.get('video')}")
    res("remake: old files removed", not (tmpdocs / old["video"]).exists() if new and new.get("video") != old.get("video") else False)
    res("remake: same story (source link kept)", new.get("source_url") == old.get("source_url"), f"{old.get('source_url', '')[:60]}")
    if new.get("video"):
        shutil.copy(tmpdocs / new["video"], out / "remake.mp4")
except Exception as e:
    res("remake path", False, f"crashed {type(e).__name__}: {str(e)[:200]}")
(out / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
print("ALLDONE", sum(1 for r in results if r["ok"]), "of", len(results), "passed", flush=True)
