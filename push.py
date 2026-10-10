"""🔔 Phone alerts: notifications from the Studio app itself (Web Push, delivered free by Apple).

The app's 🔕/🔔 button sends the phone's push address to the inbox; inbox.py keeps it in
state/push.json (addresses still in the inbox are used too, so a new 🔔 works before a run saves it).
Sending needs the private key in GitHub secret PUSH_VAPID_KEY (public half: docs/app.json "push_key").
Robot posts made 11 PM - 5 AM don't buzz the phone; ➕ Create results always do. Never fails the caller.

    python push.py --before            # start of a run: remember which posts exist
    python push.py --after [--create]  # after the app is updated: notify about the new ones
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

import schedule

ROOT = Path(__file__).parent
SUBS_FILE = ROOT / "state" / "push.json"
BEFORE_FILE = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / "kb_posts_before.json"
INBOX = json.loads((ROOT / "config.json").read_text()).get("inbox_topic", "")
KEY = os.environ.get("PUSH_VAPID_KEY", "")
CLAIMS = {"sub": "https://vikashlal12345.github.io"}   # who sends (Apple wants a site address, no path)
MAX_SUBS = 3
SKIP_FILE = BEFORE_FILE.with_name("kb_no_alert")


def saved_subs() -> list[dict]:
    try:
        return json.loads(SUBS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def add_sub(sub: dict) -> bool:
    """Keep a phone's push address (newest first). True if it's new."""
    if not (sub.get("endpoint", "").startswith("https://") and sub.get("keys", {}).get("auth")):
        return False
    subs = saved_subs()
    new = all(s["endpoint"] != sub["endpoint"] for s in subs)
    subs = [{"endpoint": sub["endpoint"], "keys": sub["keys"],
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}] + \
           [s for s in subs if s["endpoint"] != sub["endpoint"]]
    SUBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUBS_FILE.write_text(json.dumps(subs[:MAX_SUBS], indent=1))
    return new


def inbox_subs() -> list[dict]:
    """🔔 taps still in the inbox (ntfy keeps 12 hours), not saved by a robot run yet."""
    try:
        lines = requests.get(f"https://ntfy.sh/{INBOX}/json", params={"poll": "1", "since": "12h"}, timeout=20).text
    except requests.RequestException:
        return []
    out = []
    for line in lines.splitlines():
        try:
            msg = json.loads(json.loads(line).get("message", "{}"))
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(msg, dict) and msg.get("type") == "push_sub" and isinstance(msg.get("sub"), dict):
            out.insert(0, msg["sub"])
    return out


def send(title: str, body: str, post: str = "", subs: list[dict] | None = None) -> int:
    """Notify every saved phone. Returns how many got it."""
    if not KEY:
        print("  🔔 no PUSH_VAPID_KEY: phone alert not sent")
        return 0
    from pywebpush import WebPushException, webpush
    if subs is None:
        subs = saved_subs()
        subs += [s for s in inbox_subs() if all(s.get("endpoint") != o["endpoint"] for o in subs)]
    data = json.dumps({"title": title, "body": body[:240], "post": post}, ensure_ascii=False)
    sent = 0
    for s in subs[:MAX_SUBS]:
        try:
            webpush(s, data, vapid_private_key=KEY, vapid_claims=dict(CLAIMS), ttl=6 * 3600,
                    headers={"Urgency": "high"}, timeout=15)
            sent += 1
        except WebPushException as e:
            code = e.response.status_code if e.response is not None else "?"
            print(f"  🔔 alert not delivered ({code}{': phone turned alerts off' if code in (404, 410) else ''})")
        except Exception as e:
            print(f"  🔔 alert failed: {e}")
    print(f"  🔔 phone alert sent to {sent} phone(s): {title} | {body[:80]}")
    return sent


def skip() -> None:
    """This run had nothing to do (a ➕ Create request already handled): no ❌ alert."""
    SKIP_FILE.touch()


def post_ids() -> dict:
    out = {}
    for name in ("feed.json", "ready.json"):
        try:
            for p in json.loads((ROOT / "docs" / name).read_text()):
                out[p["id"]] = {**p, "_ready": name == "ready.json"}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass
    return out


def label(p: dict) -> str:
    tag = "📦 Ready" if p["_ready"] else ("🎬 Reel" if p.get("video") else (p.get("tag") or "📰 Post"))
    return f"{tag}: {p.get('headline', '').strip()}"


def after(create: bool) -> None:
    try:
        before = set(json.loads(BEFORE_FILE.read_text()))
    except (OSError, json.JSONDecodeError):
        print("  🔔 no --before list: nothing to compare")
        return
    new = [p for pid, p in post_ids().items() if pid not in before]
    new.sort(key=lambda p: p.get("created_at", ""))
    if not create and schedule.is_quiet(schedule.ist_now()):
        if new:
            print(f"  🔔 night time: {len(new)} new post(s), phone not disturbed")
        return
    if create and not new:
        if SKIP_FILE.exists():
            return
        send("❌ Your post wasn't made", "Open ⏳ Activity in the app to see why, then try again.")
    elif len(new) == 1:
        send("✅ Your post is ready" if create else "✅ New post ready", label(new[0]), new[0]["id"])
    elif new:
        send(f"✅ {len(new)} new posts ready", " · ".join(label(p) for p in new), new[-1]["id"])


def main() -> int:
    try:
        if "--before" in sys.argv:
            BEFORE_FILE.write_text(json.dumps(list(post_ids())))
            SKIP_FILE.unlink(missing_ok=True)
        elif "--after" in sys.argv:
            after("--create" in sys.argv)
        elif "--test" in sys.argv:
            send("🔔 Test alert", "Phone alerts from KhabarBawaal Studio work.")
    except Exception as e:
        print(f"  🔔 phone alert skipped: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
