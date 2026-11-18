"""Sample 'illustration' Reel (owner 18 Nov 2026, like an old-couple storybook Reel): painterly pictures + big hook, built in a temp folder, posts nothing -> toon_out/illustration_sample.mp4"""
import functools, os, shutil, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
import status
for n in ("step", "done", "fail", "info"):
    if hasattr(status, n): setattr(status, n, lambda *a, **k: None)
import reel
STYLE = ("Naive hand-painted folk illustration, imperfect wobbly lines, exaggerated expressive old faces with big noses, droopy eyes, heavy eye bags and wide happy smiles, crumpled clothes, textured gouache on rough paper, "
         "a limited warm palette of deep red, brown and amber, warm light from the lower left. Not photorealistic, not 3D, not glossy, not cute. No text, no letters, no writing, no watermark, no logos. ")
LOOK = ("an extremely old Indian husband and wife: the man with messy white hair, a long drooping face, a big nose, a bushy white moustache and heavy eye bags, in a crumpled olive kurta; "
        "the woman with thin grey hair in a bun, a deeply wrinkled face and a huge toothless joyful smile, in a faded cream floral saree")
best = {"characters": [], "allow_two": True, "need": 2, "max_scenes": 3, "wait_min": 30, "scenes": [
    {"paint": f"{LOOK}, sitting facing each other across a small table, seen from the waist up, VERY LARGE in the frame with big faces filling the lower two thirds of the picture; the man leans forward and feeds his wife a spoonful of khichdi with a trembling hand while she laughs with her whole face; behind them on the deep red wall a huge soft dark glowing silhouette of a young couple dancing close together",
     "line": "Haath kaanpte hain, par thali aaj bhi ek hi hai"},
    {"paint": f"{LOOK}, walking slowly side by side holding hands, the man leaning on a wooden walking stick, both stooped and smiling at each other, seen from the waist up, VERY LARGE in the frame, deep red and golden sunset glow behind them, a huge soft dark silhouette of young dancers high above them",
     "line": "Bas aisa hi ant chahiye 🤍"}]}
reel.TOON_STYLE = STYLE
tmp = tempfile.mkdtemp(prefix="illu_", dir=os.path.join(ROOT, "toon_out") if os.path.isdir(os.path.join(ROOT, "toon_out")) else None)
os.makedirs(os.path.join(ROOT, "toon_out"), exist_ok=True)
try:
    scenes = reel.paint_toon_scenes(best, tmp)
    keep = list(reel.LAST_TOON_GOOD)
    for n, ((img, _, _), i) in enumerate(zip(scenes, keep), 1):   # keep the raw paintings too
        img.convert("RGB").save(os.path.join(ROOT, "toon_out", f"illustration_picture{n}.jpg"), quality=92)
    out = os.path.join(tmp, "sample.mp4")
    reel.illustration_reel([(img, best["scenes"][i]["line"]) for (img, _, _), i in zip(scenes, keep)], "THE अंत I WANT", out, [6, 6])
    dst = os.path.join(ROOT, "toon_out", "illustration_sample.mp4"); shutil.copy(out, dst)
    print("RESULT OK", dst, [m for _, _, m in scenes], flush=True)
except Exception as e:
    print("RESULT FAILED", type(e).__name__, str(e)[:300], flush=True)
