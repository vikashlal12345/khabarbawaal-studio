"""Commit the robot's changes and push them, even if another run pushed meanwhile.

The hourly post, "Create post" requests and the weekly joke refresh can finish at
the same moment and all touch docs/feed.json and state/history.json. Instead of
failing on that clash, both sides are merged: posts added on either side are kept,
posts removed on either side stay removed.

    python save.py "commit message"
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

CONFIG = json.load(open("config.json"))
MAX_POSTS = CONFIG["max_posts_kept"]
KEEP_DAYS = CONFIG.get("keep_days", 3)
LIST_LIMITS = {"seen": 3000, "recent_headlines": 48, "recent_fun": 200, "fun_bank_used": 500}


def git(*args, check=True, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def stage(n: int, path: str):
    """Version of a conflicted file: 1 = common base, 2 = already pushed, 3 = ours."""
    r = git("show", f":{n}:{path}", check=False)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def merge_feed(base, pushed, ours):
    base_ids = {p["id"] for p in base or []}
    removed = (base_ids - {p["id"] for p in pushed}) | (base_ids - {p["id"] for p in ours})
    merged = {p["id"]: p for p in pushed}
    for p in ours:
        merged.setdefault(p["id"], p)
    posts = [p for p in merged.values() if p["id"] not in removed]
    posts.sort(key=lambda p: p["created_at"], reverse=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    return [p for p in posts if p["created_at"] >= cutoff][:MAX_POSTS]


def merge_state(base, pushed, ours):
    out = dict(pushed)
    for key, mine in ours.items():
        theirs = pushed.get(key)
        if isinstance(mine, list) and isinstance(theirs, list):
            out[key] = (theirs + [x for x in mine if x not in theirs])[-LIST_LIMITS.get(key, 1000):]
        elif isinstance(mine, int) and isinstance(theirs, int):
            out[key] = max(mine, theirs)
        else:
            out[key] = mine
    return out


MERGERS = {"docs/feed.json": merge_feed, "state/history.json": merge_state}


def resolve_conflicts() -> None:
    while True:
        files = git("diff", "--name-only", "--diff-filter=U").stdout.split()
        for path in files:
            if path in MERGERS:
                merged = MERGERS[path](stage(1, path), stage(2, path) or [], stage(3, path) or [])
                with open(path, "w") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=1)
            else:  # anything else (e.g. the joke bank): keep our newer version
                git("checkout", "--theirs", "--", path)
            git("add", "--", path)
            print(f"  merged {path}")
        r = git("rebase", "--continue", check=False, env={"GIT_EDITOR": "true"})
        if r.returncode == 0:
            return
        if not git("diff", "--name-only", "--diff-filter=U").stdout.strip():
            raise RuntimeError(r.stderr)


def main() -> int:
    message = sys.argv[1] if len(sys.argv) > 1 else "Update"
    git("add", "-A")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print("Nothing new to save.")
        return 0
    git("commit", "-m", message)
    for attempt in range(1, 5):
        git("fetch", "-q", "origin", "main")
        if git("rebase", "origin/main", check=False).returncode != 0:
            print("Another run saved at the same time: merging both.")
            resolve_conflicts()
        if git("push", "origin", "HEAD:main", check=False).returncode == 0:
            print(f"Saved (attempt {attempt}).")
            return 0
        time.sleep(3 * attempt)
    print("Could not save after 4 attempts.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
