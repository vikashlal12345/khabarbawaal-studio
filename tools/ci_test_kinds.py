"""CI test of the daily Reel slots (owner approved 12 Nov 2026, 'rigorous testing'): makes each slot's Reel exactly like the robot (reel.make) in a TEMP folder
(nothing is saved to the app or the repo), checks the result and copies the MP4s to ./kinds_out.
Usage: python tools/ci_test_kinds.py reel_news_early reel_break_am ..."""
import json
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
for name in ("step", "done", "fail", "info", "start"):
    if hasattr(status, name):
        setattr(status, name, lambda *a, **k: None)   # no messages to the app's Activity box
out = Path("kinds_out")
out.mkdir(exist_ok=True)
summary = []
NEW_ASKS = {"verdict", "twist", "rating", "emoji", "own_life", "predict", "finish_it", "share_friend", "save"}
for kind in sys.argv[1:]:
    feed, state = [], {}
    problems, info = [], {}
    try:
        ok = reel.make(kind, state, feed, 1, 1)
    except Exception as e:
        ok, feed = False, []
        problems.append(f"crashed: {type(e).__name__}: {str(e)[:200]}")
    if ok and feed:
        p = feed[0]
        vid = tmpdocs / p["video"]
        secs = reel.video_seconds(str(vid))
        shutil.copy(vid, out / f"{kind}.mp4")
        tags = dict(reel.TAGS)
        cap = p.get("caption", "")
        hashtags = re.findall(r"(?<!\w)#\w+", cap.split("\n\n(")[0])
        info = {"headline": p["headline"][:80], "secs": round(secs, 1), "proof": (p.get("proof") or {}).get("state"), "issues": (p.get("proof") or {}).get("issues", [])[:2],
                "ask": tags.get("relate"), "age_h": tags.get("age_h"), "pool": len(tags.get("pool", [])), "pick_no": tags.get("pick_no"), "flow": tags.get("flow"), "cover": tags.get("cover"),
                "tag": p.get("tag"), "source": p.get("source")}
        lo, hi = (11.5, 18.5) if kind in reel.BREAK_KINDS else (14.0, 24.0)
        if not lo <= secs <= hi:
            problems.append(f"length {secs:.1f}s outside {lo}-{hi}")
        if "AUTO MODE" in (p.get("tag") or ""):
            problems.append("AUTO MODE card (Claude limit?)")
        if len(hashtags) > 5:
            problems.append(f"{len(hashtags)} hashtags")
        if re.search(r"[ऀ-ॿ]", cap):
            problems.append("Devanagari in the caption")
        if tags.get("relate") not in NEW_ASKS and tags.get("relate") is not None:
            problems.append(f"old-style ask type {tags.get('relate')}")
        if re.search(r"(?i)\bteam\b.*\bya\b.*\bteam\b|\b1 ya 2\b", cap):
            problems.append("'Team A ya Team B' ask in the caption")
        if not tags.get("pool"):
            problems.append("the picker's story list was not saved in the tags")
    elif not problems:
        problems.append("make() returned False")
    summary.append({"kind": kind, "ok": bool(ok and feed and not problems), "problems": problems, **info})
    print("RESULT " + json.dumps(summary[-1], ensure_ascii=False), flush=True)
(out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print("ALLDONE", sum(1 for s in summary if s["ok"]), "of", len(summary), "passed", flush=True)
