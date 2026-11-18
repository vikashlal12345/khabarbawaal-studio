"""Paints picture 1 of the old-couple Reel in 3 different style wordings (no video, posts nothing) -> toon_out/styles/*.jpg"""
import os, random, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
import reel
out = os.path.join(ROOT, "toon_out", "styles"); os.makedirs(out, exist_ok=True)
SCENE = ("an extremely old Indian husband and wife sitting facing each other at a small table, seen from the waist up, large in the frame, the husband leans forward and feeds his wife a spoonful of khichdi with a trembling hand, "
         "she smiles with her whole face; behind them on the deep red wall a huge soft dark glowing silhouette of a young couple dancing close together")
V = {
 "A_caricature": ("Hand-drawn editorial caricature illustration in the style of a modern Indian picture book: exaggerated long drooping faces, a big nose, heavy eye bags, deep wrinkles, bushy white moustache on the man, messy white hair, "
                  "the woman with a huge toothless joyful smile, thin elongated bodies in crumpled clothes, textured gouache and pencil, a limited warm palette of deep red, brown and amber, grainy paper texture. "),
 "B_gouache": ("Textured gouache storybook illustration, warm red-brown palette, slightly clumsy hand-drawn shapes, long expressive wrinkled faces with big noses and sagging cheeks, very old people with sparse white hair, "
               "loose brush strokes, grain, glowing dark silhouette on a red wall, emotional and tender. "),
 "C_naive": ("Naive hand-painted folk illustration, imperfect wobbly lines, exaggerated old faces with big noses, droopy eyes and wide happy smiles, crumpled clothes, dark red background with a glowing black shadow of dancing young lovers, "
             "warm amber light from the lower left, painted on rough paper. "),
}
AVOID = " No text, no letters, no writing, no watermark, no logos. Not photorealistic, not 3D, not glossy, not cute."
for name, style in V.items():
    try:
        img, model = reel.flux_image(style + SCENE + AVOID, random.randint(1, 999999))
        p = os.path.join(out, f"{name}.jpg"); img.convert("RGB").save(p, quality=90)
        print("RESULT", name, "OK", model, img.size, flush=True)
    except Exception as e:
        print("RESULT", name, "FAILED", str(e)[:150], flush=True)
print("ALLDONE", flush=True)
