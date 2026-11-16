"""Test renders (NOT the app) of two AI-cartoon follower ideas (GitHub workflow toon-ideas.yml). Output: toon_out/ideas/*.mp4"""
import os, sys, tempfile, functools, shutil
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); SC = os.path.join(REPO, "toon_out")
os.makedirs(SC, exist_ok=True); sys.path.insert(0, REPO); os.chdir(REPO)
import reel, status
for n in ("step", "done", "fail", "info"):
    if hasattr(status, n): setattr(status, n, lambda *a, **k: None)
reel.TOON_STYLE = ("3D animated cartoon in the style of a funny Pixar-like Indian school comedy: big-head friendly characters with very expressive "
                   "faces, glossy render, bright colours, vertical composition with the character large in the middle and a calm uncluttered background. "
                   "No text, no letters, no writing, no signs with words, no watermark, no logos. ")
reel.toon_frame = functools.partial(reel.toon_frame, tag="AI COMEDY · CARTOON")
IDEAS = {
 "A_bollywood_bachpan_ranbir": dict(hook="BACHPAN MEIN RANBIR 😂", punch="Bachpan se hi superstar material 😂\nKal: Salman ka bachpan. Follow karo",
   characters=[{"name": "Little Ranbir", "look": "cute 8-year-old Indian schoolboy, messy dark hair, big charming grin, school uniform and school bag"}],
   scenes=[dict(paint="Little Ranbir standing in a classroom with an innocent smile, school bag on his back", line="Teacher: Homework kahan hai? 😤"),
           dict(paint="Little Ranbir looking up at the ceiling dramatically with a hand on his heart, tragic expression, classroom", line="Ranbir: Sir, dog kha gaya... Ramayana ki shooting ke liye 🐶"),
           dict(paint="Little Ranbir walking proudly with big sunglasses on and school bag on his shoulder, bright school corridor", line="Aaj bhi wohi excuse: 'Dates nahi mil rahi' 😎")]),
 "B_sharmaji_ka_beta": dict(hook="SHARMA JI KA BETA 😭", punch="Har ghar ki kahani 😭\nKal: Papa vs Cricket. Follow karo",
   characters=[{"name": "Mummy", "look": "angry Indian mother in a saree, hands on hips"}, {"name": "Sharma ji ka beta", "look": "nerdy Indian boy with round glasses and a golden trophy"}],
   scenes=[dict(paint="Mummy, an angry Indian mother in a saree, holding a chappal in her raised hand, shouting expression, home kitchen", line="Mummy: Ramayana ne records tod diye... aur tumne? Sirf screen time 😤"),
           dict(paint="Sharma ji ka beta, a nerdy Indian boy with round glasses holding a big golden trophy, huge smug smile", line="Sharma ji ka beta: Mummy, main bhi hit hoon... 98% laya hoon 🤓"),
           dict(paint="a sad slouching Indian teenage boy in a hoodie holding a report card, looking down, bedroom", line="Main: Mummy, 'Part 2' mein sudhar jaunga 😭")]),
}
out = os.path.join(SC, "ideas"); os.makedirs(out, exist_ok=True)
for name in (sys.argv[1:] or IDEAS):
    best = IDEAS[name]; tmp = tempfile.mkdtemp(prefix="toon_", dir=SC)
    try:
        sc = reel.paint_toon_scenes(best, tmp)
        mp4 = os.path.join(out, name + ".mp4")
        reel.toon_reel([(img, line) for img, line, _ in sc], best["hook"], best["punch"], mp4)
        print("RESULT", name, "OK", mp4, [m for _, _, m in sc], flush=True)
    except Exception as e:
        print("RESULT", name, "FAILED", type(e).__name__, str(e)[:200], flush=True)
print("ALLDONE", flush=True)
