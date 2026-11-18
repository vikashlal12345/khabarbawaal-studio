"""Sample 'illustration' Reel (owner 18 Nov 2026, like an old-couple storybook Reel): painterly pictures + big hook, built in a temp folder, posts nothing -> toon_out/illustration_sample.mp4"""
import functools, os, shutil, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
import status
for n in ("step", "done", "fail", "info"):
    if hasattr(status, n): setattr(status, n, lambda *a, **k: None)
import reel
STYLE = ("A warm hand-painted storybook illustration with soft visible brush strokes, flat expressive shapes, deep crimson, brown and amber tones, gentle glowing light, tender emotional mood, "
         "vertical composition: the two people LARGE in the MIDDLE of the frame with their faces at mid-height, calm empty dark space above their heads. No text, no letters, no writing, no signs, no watermark, no logos. ")
LOOK = "an elderly Indian couple: the husband with white hair and a white moustache in a plain olive kurta, the wife with grey hair in a bun wearing a light cream floral saree"
best = {"characters": [], "allow_two": True, "need": 2, "max_scenes": 3, "wait_min": 30, "scenes": [
    {"paint": f"{LOOK}, sitting close together at a small table, seen from the waist up, large in the frame; the husband gently feeds his smiling wife a spoonful of khichdi with a spoon; behind them on the dark red wall a big soft glowing shadow of the same couple as young people dancing together",
     "line": "Aakhri saans tak, ek hi thali mein"},
    {"paint": f"{LOOK}, seen from the waist up, large in the frame, holding hands and smiling softly at each other, golden sunset glow behind them, faint silhouettes of two young dancers in the warm sky high above them",
     "line": "Bas aisa hi ant chahiye 🤍"}]}
reel.TOON_STYLE = STYLE
tmp = tempfile.mkdtemp(prefix="illu_", dir=os.path.join(ROOT, "toon_out") if os.path.isdir(os.path.join(ROOT, "toon_out")) else None)
os.makedirs(os.path.join(ROOT, "toon_out"), exist_ok=True)
try:
    scenes = reel.paint_toon_scenes(best, tmp)
    keep = list(reel.LAST_TOON_GOOD)
    out = os.path.join(tmp, "sample.mp4")
    reel.illustration_reel([(img, best["scenes"][i]["line"]) for (img, _, _), i in zip(scenes, keep)], "THE ANTH I WANT", out, [6, 6])
    dst = os.path.join(ROOT, "toon_out", "illustration_sample.mp4"); shutil.copy(out, dst)
    print("RESULT OK", dst, [m for _, _, m in scenes], flush=True)
except Exception as e:
    print("RESULT FAILED", type(e).__name__, str(e)[:300], flush=True)
