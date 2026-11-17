"""Reads every news feed in config.json once (from GitHub's computer) and prints which ones work. Posts nothing."""
import os, sys, time, requests, feedparser
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import generate as g
ok = bad = 0
for f in g.CONFIG["feeds"]:
    try:
        r = requests.get(f["url"], headers={"User-Agent": g.UA}, timeout=20)
        n = len(feedparser.parse(r.content).entries)
        img = "?"
        print(f"{'OK ' if r.status_code == 200 and n else 'BAD'} {f['name']:22} {f.get('category',''):14} http {r.status_code} items {n}", flush=True)
        ok += r.status_code == 200 and n > 0
        bad += not (r.status_code == 200 and n > 0)
    except Exception as e:
        print(f"BAD {f['name']:22} {f.get('category',''):14} {str(e)[:70]}", flush=True)
        bad += 1
cands = g.fetch_candidates(set())
import collections
print("CANDIDATES", len(cands), dict(collections.Counter(c["source"] + "/" + c["category"] for c in cands)))
print("FEED_ERRORS", g.FEED_ERRORS)
print(f"SUMMARY ok={ok} bad={bad}")
