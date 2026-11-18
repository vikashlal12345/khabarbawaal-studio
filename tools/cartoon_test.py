"""Builds a ➕ Create cartoon request in a temp folder (posts nothing): python tools/cartoon_test.py tools/cartoon_tests/gta.txt  -> toon_out/cartoon_<name>.mp4 + caption"""
import os, shutil, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
import status
for n in ("step", "done", "fail", "info"):
    if hasattr(status, n): setattr(status, n, lambda *a, **k: None)
import cartoon_create
out = os.path.join(ROOT, "toon_out"); os.makedirs(out, exist_ok=True)
for f in sys.argv[1:]:
    raw = open(f).read()
    name = os.path.splitext(os.path.basename(f))[0]
    tmp = tempfile.mkdtemp(prefix="cart_", dir=out)
    try:
        cover, mp4, entry = cartoon_create.build(raw, tmp, lambda t, n=0: print("STEP", t, flush=True))
        dst = os.path.join(out, f"cartoon_{name}.mp4"); shutil.copy(mp4, dst)
        print("RESULT", name, "OK", dst, "| hook:", entry["headline"], "\nCAPTION:", entry["caption"], "\nPIN:", entry["pin_comment"], flush=True)
    except Exception as e:
        print("RESULT", name, "FAILED", type(e).__name__, str(e)[:300], flush=True)
print("ALLDONE", flush=True)
