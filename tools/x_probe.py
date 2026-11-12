"""Manual probe: can GitHub's computers read @Somnath_Temple's videos from X? Tries several times; prints the HTTP status and what it found. Posts nothing."""
import json
import re
import sys
import time

import requests

handle = sys.argv[1] if len(sys.argv) > 1 else "Somnath_Temple"
for attempt in range(1, 7):
    r = requests.get(f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}", headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    n_posts = n_vid = 0
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if m:
        try:
            ents = json.loads(m.group(1))["props"]["pageProps"]["timeline"]["entries"]
            n_posts = len(ents)
            n_vid = sum(1 for e in ents if any(x.get("type") == "video" for x in e.get("content", {}).get("tweet", {}).get("extended_entities", {}).get("media", [])))
        except Exception as e:
            print("parse error", str(e)[:80])
    print(f"attempt {attempt}: HTTP {r.status_code}, posts={n_posts}, videos={n_vid}, limit-remaining={r.headers.get('x-rate-limit-remaining')}, reset={r.headers.get('x-rate-limit-reset')}", flush=True)
    if r.status_code == 200 and n_vid:
        break
    time.sleep(45)
