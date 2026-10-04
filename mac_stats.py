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
    git("config", "user.name", "stats-bot")
    git("config", "user.email", "stats-bot@users.noreply.github.com")
    r = subprocess.run(["python3", "save.py", "Stats update from Mac"], cwd=CLONE, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip()[:200])


if __name__ == "__main__":
    main()
