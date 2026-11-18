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
LOOK = ("a very old, frail Indian couple in their late eighties: the husband bald on top with thin white hair, deeply wrinkled face, sunken cheeks, thick round glasses, a hunched back, in a slightly crumpled olive kurta; "
        "the wife with thin grey hair in a small bun, deeply wrinkled, a slightly bent back, in a worn cream cotton saree; both with thin shaky hands, clearly very old and a little clumsy, but warm and close")
best = {"characters": [], "allow_two": True, "need": 2, "max_scenes": 3, "wait_min": 30, "scenes": [
    {"paint": f"{LOOK}, sitting close together at a small table, seen from the waist up, large in the frame; the husband's trembling hand feeds his laughing wife a spoonful of khichdi and a few grains of rice have dropped on his kurta, she gently wipes his chin with the end of her saree; behind them on the dark red wall a big soft glowing shadow of the same couple as young people dancing together",
     "line": "Haath kaanpte hain, par thali aaj bhi ek hi hai"},
    {"paint": f"{LOOK}, walking slowly side by side holding hands, the husband leaning on a wooden walking stick, both stooped and smiling at each other, seen from the waist up, large in the frame, golden sunset glow behind them, faint silhouettes of two young dancers in the warm sky high above them",
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
