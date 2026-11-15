"""Claude plan usage (% of the 5-hour window and of the week), recorded after each robot run.

Anthropic shows plan limits only as a percentage (claude.ai > Settings > Usage). Claude Code reads
the same numbers from an endpoint that isn't officially documented, so this may stop working;
every failure is silent and the Stats page then shows "not available".
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

PATH = Path(__file__).parent / "docs" / "stats" / "limits.json"


def fetch() -> dict | None:
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        return None
    try:
        r = requests.get("https://api.anthropic.com/api/oauth/usage", timeout=15, headers={
            "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code/2.1", "Content-Type": "application/json"})
        if r.status_code != 200:
            print(f"  limits: HTTP {r.status_code} {r.text[:120]}")
            return None
        return r.json()
    except Exception as e:
        print(f"  limits: {str(e)[:120]}")
        return None


def record() -> None:
    data = fetch()
    if not data:
        return
    def window(key):
        w = data.get(key) or {}
        return {"pct": w.get("utilization"), "resets_at": w.get("resets_at")} if w else None
    snap = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "five_hour": window("five_hour"), "seven_day": window("seven_day")}
    print(f"  limits: 5h {snap['five_hour']} | week {snap['seven_day']}")
    PATH.parent.mkdir(parents=True, exist_ok=True)
    hist = json.loads(PATH.read_text()) if PATH.exists() else []
    hist = (hist + [snap])[-700:]  # about 2 weeks of runs
    PATH.write_text(json.dumps(hist, indent=1))
