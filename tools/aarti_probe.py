"""Manual probe (owner approved 12 Nov 2026): which free Pixabay videos exist for each god / festival aarti? Prints hits; posts nothing."""
import json
import os
import sys

import requests

QUERIES = sys.argv[1:] or ["aarti", "temple aarti", "ganga aarti", "diya aarti", "shiva temple", "shivling", "hanuman", "hanuman temple", "ganesh", "ganesh idol", "ganpati",
                           "vishnu", "krishna", "lakshmi puja", "diwali puja", "durga", "durga puja", "navratri", "shani", "surya", "sun rise temple", "ram mandir",
                           "ayodhya", "kashi", "varanasi aarti", "haridwar aarti", "indian temple", "hindu temple", "oil lamp", "diwali diya", "holi", "janmashtami"]
key = os.environ["PIXABAY_API_KEY"]
for q in QUERIES:
    r = requests.get("https://pixabay.com/api/videos/", params={"key": key, "q": q, "per_page": 20, "safesearch": "true"}, timeout=30)
    hits = r.json().get("hits", []) if r.status_code == 200 else []
    print(f"## {q}: HTTP {r.status_code}, {len(hits)} hits", flush=True)
    for h in hits[:20]:
        v = h.get("videos", {})
        big = v.get("large") or v.get("medium") or {}
        print("HIT " + json.dumps({"q": q, "id": h["id"], "dur": h.get("duration"), "tags": h.get("tags"), "w": big.get("width"), "h": big.get("height"),
                                   "url": big.get("url"), "med": (v.get("medium") or {}).get("url"), "pic": h.get("picture_id"), "user": h.get("user"),
                                   "page": h.get("pageURL"), "likes": h.get("likes"), "views": h.get("views")}, ensure_ascii=False), flush=True)
