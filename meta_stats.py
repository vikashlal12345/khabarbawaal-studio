"""📊 Instagram numbers from Meta's OFFICIAL Graph API (owner's choice, 29 Oct 2026; replaces screen recordings).

    META_IG_TOKEN=... python meta_stats.py [--probe]

Read-only: followers, and for every recent post its views / reach / likes / comments / shares / saves / reposts.
Writes docs/stats/instagram_api.json. The token (GitHub secret META_IG_TOKEN, system-user token, never expires)
is never printed. Account id: config.json `ig_user_id` (owner's @khabarbawaal, from the app's token page)."""
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


def main() -> int:
    probe = "--probe" in sys.argv
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
