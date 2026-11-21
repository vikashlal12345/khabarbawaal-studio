"""Read-only deep export of our own Instagram Reels for analysis (owner 21 Nov 2026: 'extensive research, why new Reels do not do well').
Uses the same read-only Meta Graph API token as meta_stats.py (META_IG_TOKEN). Writes ./deep_out/:
  reels.json  - every Reel of the last N days: id, time, full caption, permalink, duration, views / reach / likes / comments / shares / saves / watch time / total_interactions
  frames/<id>_<k>.jpg - small frames of each Reel (0.4 s, 1.5 s, 3 s, 6 s, 10 s, last 2 s) taken from Instagram's own video file for that Reel
  account.json - day by day account reach / views split into followers / non-followers, follower count, follows & unfollows, accounts engaged
Posts nothing, changes nothing.  python tools/meta_deep.py 40"""
import json, os, re, subprocess, sys, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meta_stats as M

days = int(sys.argv[1]) if len(sys.argv) > 1 else 40
only = sys.argv[2] if len(sys.argv) > 2 else "all"     # all | reels | account
out = Path("deep_out"); (out / "frames").mkdir(parents=True, exist_ok=True)
try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FF = "ffmpeg"
host = None
for h in M.HOSTS:
    st, acc = M.get(h, M.IG_ID, {"fields": "username"})
    if st == 200:
        host = h
        break
if not host:
    print("no host works"); sys.exit(1)
now = datetime.now(timezone.utc)
cutoff = now - timedelta(days=days)

if only == "comments":
    after, ids = None, []
    while True:
        p = {"fields": "id,timestamp,media_product_type,caption", "limit": 50}
        if after:
            p["after"] = after
        st, media = M.get(host, f"{M.IG_ID}/media", p)
        if st != 200:
            print("media error", M.err(media)); break
        data = media.get("data", [])
        ids += [(x["id"], x["timestamp"], (x.get("caption") or "")[:60]) for x in data]
        after = ((media.get("paging") or {}).get("cursors") or {}).get("after")
        if not data or not after or datetime.strptime(data[-1]["timestamp"], "%Y-%m-%dT%H:%M:%S%z") < cutoff:
            break
    res = []
    for mid, ts, cap in ids:
        if datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z") < cutoff:
            continue
        st, body = M.get(host, f"{mid}/comments", {"fields": "id,text,timestamp,username,like_count,replies.limit(5){text,username,timestamp}", "limit": 30})
        res.append({"id": mid, "time": ts, "cap": cap, "status": st, "comments": body.get("data", []) if st == 200 else M.err(body)[:100]})
    json.dump(res, open(out / "comments.json", "w"), ensure_ascii=False)
    print("COMMENTS saved:", len(res), "with comments:", sum(1 for r in res if isinstance(r["comments"], list) and r["comments"]), flush=True)
    sys.exit(0)

if only == "competitors":
    import json as _j
    cfg = _j.load(open("config.json"))
    names = cfg.get("competitors") or []
    names = [n["username"] if isinstance(n, dict) else n for n in names]
    comp = {}
    for nm in names:
        fields = (f"business_discovery.username({nm}){{username,followers_count,media_count,media.limit(50){{timestamp,like_count,comments_count,media_product_type}}}}")
        st, body = M.get(host, M.IG_ID, {"fields": fields})
        if st != 200:
            print("COMP", nm, "ERR", M.err(body)[:80]); continue
        bd = body.get("business_discovery") or {}
        comp[nm] = {"followers": bd.get("followers_count"), "media": (bd.get("media") or {}).get("data", [])}
        print("COMP", nm, bd.get("followers_count"), len(comp[nm]["media"]), flush=True)
    json.dump(comp, open(out / "competitors_raw.json", "w"), ensure_ascii=False)
    sys.exit(0)

if only in ("all", "account"):
    acct = {"daily": []}
    for k in range(days, -1, -1):
        d0 = (now - timedelta(days=k)).replace(hour=0, minute=0, second=0, microsecond=0)
        since, until = int(d0.timestamp()), int((d0 + timedelta(days=1)).timestamp())
        row = {"day": d0.strftime("%Y-%m-%d")}
        for metric, bd in (("reach", None), ("reach", "follow_type"), ("views", "follow_type"), ("views", None), ("accounts_engaged", None), ("total_interactions", None), ("follows_and_unfollows", "follow_type"), ("likes", None), ("shares", None), ("saves", None), ("comments", None)):
            p = {"metric": metric, "period": "day", "metric_type": "total_value", "since": since, "until": until}
            if bd:
                p["breakdown"] = bd
            st, body = M.get(host, f"{M.IG_ID}/insights", p)
            key = metric + (f"_{bd}" if bd else "")
            if st != 200:
                row[key] = "ERR " + M.err(body)[:90]
                continue
            for dd in body.get("data", []):
                tv = dd.get("total_value", {})
                if tv.get("breakdowns"):
                    row[key] = {r["dimension_values"][0]: r["value"] for r in tv["breakdowns"][0].get("results", [])}
                else:
                    row[key] = tv.get("value")
        acct["daily"].append(row)
    st, body = M.get(host, f"{M.IG_ID}/insights", {"metric": "follower_count", "period": "day", "since": int((now - timedelta(days=min(days, 29))).timestamp()), "until": int(now.timestamp())})
    acct["follower_count_raw"] = body.get("data", body)
    st, body = M.get(host, f"{M.IG_ID}/insights", {"metric": "reach", "period": "day", "metric_type": "total_value", "breakdown": "media_product_type", "since": int((now - timedelta(days=28)).timestamp()), "until": int(now.timestamp())})
    acct["reach_by_surface_28d"] = body.get("data", body)
    json.dump(acct, open(out / "account.json", "w"), ensure_ascii=False, indent=1)
    print("ACCOUNT days:", len(acct["daily"]), "| sample:", json.dumps(acct["daily"][-1], ensure_ascii=False)[:400], flush=True)

if only in ("all", "reels"):
    after, rows = None, []
    while True:
        p = {"fields": "id,caption,media_product_type,media_type,permalink,timestamp,like_count,comments_count,media_url,thumbnail_url", "limit": 50}
        if after:
            p["after"] = after
        st, media = M.get(host, f"{M.IG_ID}/media", p)
        if st != 200:
            print("media error", M.err(media)); break
        data = media.get("data", [])
        rows += data
        after = ((media.get("paging") or {}).get("cursors") or {}).get("after")
        if not data or not after or datetime.strptime(data[-1]["timestamp"], "%Y-%m-%dT%H:%M:%S%z") < cutoff:
            break
    res = []
    for n, x in enumerate(rows):
        ts = datetime.strptime(x["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        if ts < cutoff:
            continue
        st2, ins = M.get(host, f"{x['id']}/insights", {"metric": "views,reach,likes,comments,shares,saved,reposts,ig_reels_avg_watch_time,ig_reels_video_view_total_time,total_interactions"})
        if st2 != 200:
            st2, ins = M.get(host, f"{x['id']}/insights", {"metric": "views,reach,likes,comments,shares,saved,ig_reels_avg_watch_time"})
        v = {d["name"]: (d.get("values") or [{}])[0].get("value") for d in ins.get("data", [])} if st2 == 200 else {}
        rec = {"id": x["id"], "time": x["timestamp"], "type": x.get("media_product_type"), "caption": x.get("caption") or "", "permalink": x.get("permalink"), **v}
        th = x.get("thumbnail_url")
        if th:   # the cover image Instagram shows (first frame / chosen cover), resized small
            try:
                tf = out / f"th_{x['id']}.src"
                urllib.request.urlretrieve(th, tf)
                subprocess.run([FF, "-loglevel", "error", "-i", str(tf), "-vf", "scale=270:-1", "-q:v", "6", str(out / "frames" / f"{x['id']}_cover.jpg"), "-y"], capture_output=True)
                tf.unlink()
            except Exception as e:
                rec["thumb_error"] = str(e)[:80]
        rec["has_media_url"] = bool(x.get("media_url"))
        url = x.get("media_url")
        dur = None
        if url and x.get("media_product_type") == "REELS":
            f = out / f"v_{x['id']}.mp4"
            try:
                urllib.request.urlretrieve(url, f)
                r = subprocess.run([FF, "-i", str(f)], capture_output=True, text=True)
                m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
                if m:
                    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                times = [0.4, 1.5, 3, 6, 10] + ([max(0.5, dur - 2)] if dur else [])
                for k, t in enumerate(times):
                    subprocess.run([FF, "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(f), "-frames:v", "1", "-vf", "scale=270:-1", "-q:v", "6", str(out / "frames" / f"{x['id']}_{k}.jpg"), "-y"], capture_output=True)
            except Exception as e:
                rec["video_error"] = str(e)[:80]
            finally:
                try:
                    f.unlink()
                except Exception:
                    pass
        rec["duration"] = dur
        res.append(rec)
        if n % 25 == 0:
            print(f"  {n}/{len(rows)} done", flush=True)
    json.dump(res, open(out / "reels.json", "w"), ensure_ascii=False)
    print("REELS saved:", len(res), flush=True)
