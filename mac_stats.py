"""Runs hourly on the Mac (launchd): sends two things to the app's Stats page.

1. Plan usage %: the Claude login on this Mac can read the same numbers as
   claude.ai > Settings > Usage. Only the percentages are uploaded; the login never leaves the Mac.
2. Ad hoc Claude Code usage: token totals per day from Claude Code's local logs
   (~/.claude/projects). claude.ai website/phone chats aren't in those logs.

Works in its own copy of the repo (~/.khabarbawaal-stats) so it never touches your working folder.
    python3 mac_stats.py
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

REPO = "https://github.com/vikashlal12345/khabarbawaal-studio.git"
CLONE = Path.home() / ".khabarbawaal-stats"
LOGS = Path.home() / ".claude" / "projects"
IST = timedelta(hours=5, minutes=30)


def git(*args, cwd=CLONE, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def plan_usage() -> dict | None:
    raw = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                         capture_output=True, text=True)
    if raw.returncode != 0:
        print("no Claude login in keychain")
        return None
    token = json.loads(raw.stdout).get("claudeAiOauth", {}).get("accessToken", "")
    r = requests.get("https://api.anthropic.com/api/oauth/usage", timeout=15, headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "User-Agent": "claude-code/2.1"})
    if not r.ok:
        print("plan usage:", r.status_code)
        return None
    d = r.json()
    win = lambda k: {"pct": d[k]["utilization"], "resets_at": d[k]["resets_at"]} if d.get(k) else None
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "five_hour": win("five_hour"), "seven_day": win("seven_day")}


def adhoc_usage(days: int = 35) -> dict:
    """Tokens per IST day from Claude Code logs (each reply counted once)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen, out = set(), {}
    for f in LOGS.rglob("*.jsonl"):
        if datetime.fromtimestamp(f.stat().st_mtime, timezone.utc) < cutoff:
            continue
        for line in f.open(errors="ignore"):
            if '"usage"' not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = e.get("message") or {}
            u = msg.get("usage")
            if e.get("type") != "assistant" or not u:
                continue
            key = (msg.get("id"), e.get("requestId"))
            if key in seen:
                continue
            seen.add(key)
            ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
            if ts < cutoff:
                continue
            day = (ts + IST).date().isoformat()
            t = out.setdefault(day, {"replies": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
            t["replies"] += 1
            t["input"] += u.get("input_tokens", 0)
            t["output"] += u.get("output_tokens", 0)
            t["cache_read"] += u.get("cache_read_input_tokens", 0)
            t["cache_write"] += u.get("cache_creation_input_tokens", 0)
    return dict(sorted(out.items()))


IG_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"


def ig_count(text: str, word: str) -> int:
    import re
    m = re.search(rf"([\d.,]+)\s*([KM]?)\s+{word}", text)
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    return int(n * {"K": 1e3, "M": 1e6}.get(m.group(2), 1))


def instagram(stats: Path, handle: str = "khabarbawaal") -> None:
    """Followers (public profile) + likes/comments of linked posts (public post pages).
    Instagram blocks servers, so this runs from the Mac. At most every 3 hours."""
    import re
    path = stats / "instagram.json"
    data = json.loads(path.read_text()) if path.exists() else {"followers": [], "posts": {}}
    last = data["followers"][-1]["at"] if data["followers"] else "2000-01-01T00:00:00+00:00"
    if datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=3):
        return
    meta = lambda html: (re.search(r'og:description" content="([^"]+)', html) or re.search(r'name="description" content="([^"]+)', html))
    try:
        prof = requests.get(f"https://www.instagram.com/{handle}/", headers={"User-Agent": IG_UA}, timeout=20).text
        m = meta(prof)
        if m:
            data["followers"].append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "followers": ig_count(m.group(1), "Followers"), "posts": ig_count(m.group(1), "Posts")})
            data["followers"] = data["followers"][-500:]
    except requests.RequestException as e:
        print("instagram profile:", e)
    links = json.loads((stats / "ig_links.json").read_text()) if (stats / "ig_links.json").exists() else {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    for pid, info in links.items():
        if info.get("linked_at") and datetime.fromisoformat(info["linked_at"]) < cutoff:
            continue
        try:
            html = requests.get(info["url"], headers={"User-Agent": IG_UA}, timeout=20).text
        except requests.RequestException:
            continue
        m = meta(html)
        if m:
            data["posts"][pid] = {"likes": ig_count(m.group(1), "likes"), "comments": ig_count(m.group(1), "comments"),
                                  "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path.write_text(json.dumps(data, indent=1))
    print(f"instagram: {len(data['posts'])} posts checked, followers {data['followers'][-1]['followers'] if data['followers'] else '?'}")


def main() -> None:
    if not CLONE.exists():
        git("clone", "-q", "--depth", "1", REPO, str(CLONE), cwd=Path.home())
    git("fetch", "-q", "origin", "main")
    git("reset", "-q", "--hard", "origin/main")  # history is replaced weekly; always start fresh
    stats = CLONE / "docs" / "stats"
    stats.mkdir(parents=True, exist_ok=True)
    snap = plan_usage()
    if snap:
        path = stats / "limits.json"
        hist = json.loads(path.read_text()) if path.exists() else []
        path.write_text(json.dumps((hist + [snap])[-700:], indent=1))
        print("plan:", snap["five_hour"], snap["seven_day"])
    (stats / "adhoc.json").write_text(json.dumps(adhoc_usage(), indent=1))
    try:
        instagram(stats)
    except Exception as e:
        print("instagram:", e)
    git("config", "user.name", "stats-bot")
    git("config", "user.email", "stats-bot@users.noreply.github.com")
    r = subprocess.run(["python3", "save.py", "Stats update from Mac"], cwd=CLONE, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip()[:200])


if __name__ == "__main__":
    main()
