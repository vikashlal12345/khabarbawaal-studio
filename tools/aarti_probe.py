"""Manual probe (owner approved 12 Nov 2026): which free Pixabay videos exist for each god / festival aarti? Prints hits; posts nothing.
No arguments: every search in tools/aarti_queries.json (group -> searches); arguments: just those searches (group 'adhoc')."""
import json
import os
import sys
import time
from pathlib import Path

import requests

args = sys.argv[1:]
if args:
    groups = {"adhoc": args}
else:
    groups = json.load(open(Path(__file__).parent / "aarti_queries.json"))
key = os.environ["PIXABAY_API_KEY"]
for group, queries in groups.items():
    for q in queries:
        r = requests.get("https://pixabay.com/api/videos/", params={"key": key, "q": q, "per_page": 30, "safesearch": "true"}, timeout=30)
        hits = r.json().get("hits", []) if r.status_code == 200 else []
        print(f"## {group} / {q}: HTTP {r.status_code}, {len(hits)} hits", flush=True)
        for h in hits:
            v = h.get("videos", {})
            big = v.get("large") or v.get("medium") or {}
            print("HIT " + json.dumps({"g": group, "q": q, "id": h["id"], "dur": h.get("duration"), "tags": h.get("tags"), "w": big.get("width"), "h": big.get("height"),
                                       "url": big.get("url"), "med": (v.get("medium") or {}).get("url"),
                                       "thumb": big.get("thumbnail") or (v.get("medium") or {}).get("thumbnail") or (v.get("small") or {}).get("thumbnail"),
                                       "user": h.get("user"), "page": h.get("pageURL")}, ensure_ascii=False), flush=True)
        if r.status_code == 429:
            time.sleep(30)
        time.sleep(0.8)   # Pixabay allows 100 requests a minute
