"""📊 Instagram numbers from Meta's OFFICIAL Graph API (owner's choice, 29 Oct 2026; replaces screen recordings).

    META_IG_TOKEN=... python meta_stats.py [--probe]

Read-only: followers, and for every recent post its views / reach / likes / comments / shares / saves / reposts.
Writes docs/stats/instagram_api.json. The token (GitHub secret META_IG_TOKEN, system-user token, never expires)
is never printed. Account id: config.json `ig_user_id` (owner's @khabarbawaal, from the app's token page)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

IG_ID = "17841423915208770"
HOSTS = ["https://graph.facebook.com/v23.0", "https://graph.instagram.com/v23.0"]
OUT = "docs/stats/instagram_api.json"
# newest names first; older apps call 'saves' saved
METRIC_SETS = ["views,reach,likes,comments,shares,saved,reposts,ig_reels_avg_watch_time,total_interactions",
               "views,reach,likes,comments,shares,saved,reposts", "views,reach,likes,comments,shares,saved", "reach,likes,comments,shares,saved", "reach,likes,comments,shares"]


def get(host: str, path: str, params: dict) -> tuple[int, dict]:
    q = urllib.parse.urlencode({**params, "access_token": os.environ["META_IG_TOKEN"]})
    try:
        with urllib.request.urlopen(f"{host}/{path}?{q}", timeout=40) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except Exception:
            body = {}
        return e.code, body
    except Exception as e:
        return 0, {"error": {"message": str(e)[:200]}}


def err(body: dict) -> str:
    e = body.get("error", {})
    return f"{e.get('code', '')} {e.get('message', '')}"[:200].replace(os.environ["META_IG_TOKEN"], "***")


def discover(host: str, username: str, limit: int = 30) -> dict | None:
    """📡 Meta's official Business Discovery: the PUBLIC numbers of another Business / Creator account (followers, recent posts with likes and
    comments; never views): read-only, no scraping, no copying of posts. None + a printed reason when Meta refuses."""
    fields = (f"business_discovery.username({username}){{username,name,followers_count,media_count,"
              f"media.limit({limit}){{caption,like_count,comments_count,timestamp,media_type,media_product_type,permalink}}}}")
    st, body = get(host, IG_ID, {"fields": fields})
    if st != 200:
        print(f"  discovery @{username}: HTTP {st} - {err(body)}")
        return None
    bd = body.get("business_discovery") or {}
    media = (bd.get("media") or {}).get("data", [])
    print(f"  discovery @{username}: {bd.get('followers_count')} followers, {len(media)} posts")
    return {"username": bd.get("username", username), "name": bd.get("name"), "followers": bd.get("followers_count"),
            "media_count": bd.get("media_count"),
            "media": [{"caption": (x.get("caption") or "")[:200], "likes": x.get("like_count"), "comments": x.get("comments_count"),
                       "time": x.get("timestamp"), "type": x.get("media_product_type") or x.get("media_type"), "permalink": x.get("permalink")}
                      for x in media]}


COMP = "docs/stats/competitors.json"


def read_competitors(host: str, force: bool = False) -> None:
    """📡 Once a day: the public numbers of the pages in config.json `competitors` (Meta's official Business Discovery) -> docs/stats/competitors.json:
    per page followers, average / median likes of its last 30 posts, and its OUTLIERS of the last 21 days (posts with 2x+ the page's median likes):
    what is working on the big Bollywood / viral pages right now. Patterns only: nothing is copied."""
    import statistics
    try:
        old = json.load(open(COMP))
        if not force and (datetime.now(timezone.utc) - datetime.fromisoformat(old["updated"])).total_seconds() < 20 * 3600:
            return
    except Exception:
        pass
    try:
        names = json.load(open("config.json")).get("competitors", [])
    except Exception:
        names = []
    pages = []
    now = datetime.now(timezone.utc)
    for n in names:
        d = discover(host, n, 30)
        if not d or not d["media"]:
            continue
        likes = [x["likes"] for x in d["media"] if x.get("likes") is not None]
        if len(likes) < 8:
            continue
        med = statistics.median(likes)
        top = []
        for x in d["media"]:
            try:
                age = (now - datetime.strptime(x["time"], "%Y-%m-%dT%H:%M:%S%z")).total_seconds() / 86400
            except Exception:
                continue
            if age <= 21 and x.get("likes") is not None and med > 0 and x["likes"] >= 2 * med:
                top.append({**x, "ratio": round(x["likes"] / med, 1), "caption": x["caption"].split("\n")[0][:140]})
        top.sort(key=lambda x: -x["ratio"])
        pages.append({"username": d["username"], "followers": d["followers"], "avg_likes": round(statistics.mean(likes)), "median_likes": round(med),
                      "posts": len(likes), "top": top[:5]})
    if pages:
        json.dump({"updated": now.isoformat(timespec="seconds"), "pages": pages}, open(COMP, "w"), ensure_ascii=False, indent=1)
        print(f"competitors: {len(pages)} pages saved")


def main() -> int:
    probe = "--probe" in sys.argv
    if "--discover" in sys.argv:   # python meta_stats.py --discover name1,name2
        names = [n.strip().lstrip("@") for n in sys.argv[sys.argv.index("--discover") + 1].split(",") if n.strip()]
        for h in HOSTS:
            st, acc = get(h, IG_ID, {"fields": "username"})
            if st == 200:
                if names == ["all"]:
                    read_competitors(h, force=True)
                    return 0
                for n in names:
                    discover(h, n, 5)
                return 0
        return 1
    host = None
    for h in HOSTS:
        st, acc = get(h, IG_ID, {"fields": "username,followers_count,follows_count,media_count"})
        print(f"{h.split('/')[2]}: account HTTP {st}" + ("" if st == 200 else f" - {err(acc)}"))
        if st == 200:
            host = h
            break
    if not host:
        if probe:   # explain a refusal: who is the token, what may it do
            for path, f in (("me", "id,name"), ("me/permissions", ""), ("me/accounts", "id,name,instagram_business_account"),
                            ("me/assigned_instagram_accounts", "id,username"), (IG_ID, "id"), ("debug_token", "")):
                p = {"fields": f} if f else {}
                if path == "debug_token":
                    p = {"input_token": os.environ["META_IG_TOKEN"]}
                st, body = get(HOSTS[0], path, p)
                txt = json.dumps(body, ensure_ascii=False).replace(os.environ["META_IG_TOKEN"], "***")
                print(f"  {path}: HTTP {st} {txt[:500]}")
        return 1
    if probe:   # account-level daily numbers (Reels have no per-Reel follows metric)
        import time as _t
        for metric in ("follows_and_unfollows", "reach", "profile_views", "accounts_engaged"):
            st, body = get(host, f"{IG_ID}/insights", {"metric": metric, "period": "day", "metric_type": "total_value",
                                                       "since": int(_t.time()) - 3 * 86400, "until": int(_t.time())})
            print(f"  account insight {metric}: HTTP {st} {json.dumps(body)[:260] if st == 200 else err(body)}")
    print(f"account: @{acc.get('username')} followers={acc.get('followers_count')} media={acc.get('media_count')}")
    st, media = get(host, f"{IG_ID}/media", {"fields": "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count,thumbnail_url",
                                              "limit": 50})
    print(f"media list HTTP {st}" + ("" if st == 200 else f" - {err(media)}"))
    if st != 200:
        return 1
    items = media.get("data", [])
    print(f"{len(items)} posts")
    rows = []
    metric_ok = None
    for m in items[: (3 if probe else 50)]:
        row = {"id": m["id"], "permalink": m.get("permalink"), "caption": (m.get("caption") or "")[:140], "time": m.get("timestamp"),
               "type": m.get("media_product_type") or m.get("media_type"), "thumb": m.get("thumbnail_url"), "likes": m.get("like_count"), "comments": m.get("comments_count")}
        for ms in (METRIC_SETS[METRIC_SETS.index(metric_ok):] if metric_ok else METRIC_SETS):
            st, ins = get(host, f"{m['id']}/insights", {"metric": ms})
            if st == 200:
                metric_ok = ms
                for d in ins.get("data", []):
                    v = (d.get("values") or [{}])[0].get("value", d.get("total_value", {}).get("value"))
                    row["saves" if d["name"] in ("saved", "saves") else d["name"]] = v
                break
            if probe:
                print(f"  insights [{ms}] HTTP {st} - {err(ins)}")
        rows.append(row)
    print("metrics that worked:", metric_ok)
    if probe:
        print(json.dumps(rows[:2], ensure_ascii=False)[:900])
        return 0
    # follower history for the Growth chart (docs/stats/instagram.json, the old file the page already reads): one point per ~45 min
    hist_path = "docs/stats/instagram.json"
    try:
        hist = json.load(open(hist_path))
    except Exception:
        hist = {"followers": [], "posts": {}}
    nowiso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fl = hist.setdefault("followers", [])
    if acc.get("followers_count") is not None and (not fl or (datetime.fromisoformat(nowiso) - datetime.fromisoformat(fl[-1]["at"])).total_seconds() > 2700):
        fl.append({"at": nowiso, "followers": acc["followers_count"], "posts": acc.get("media_count")})
        hist["followers"] = fl[-600:]
        json.dump(hist, open(hist_path, "w"), ensure_ascii=False, indent=1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "account": {
        "username": acc.get("username"), "followers": acc.get("followers_count"), "media_count": acc.get("media_count")},
        "media": rows}, open(OUT, "w"), ensure_ascii=False, indent=1)
    print("saved", OUT, len(rows))
    read_competitors(host)
    return 0


if __name__ == "__main__":
    sys.exit(main())
