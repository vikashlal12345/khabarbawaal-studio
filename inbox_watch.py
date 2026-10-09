"""Wait until the next posting slot while checking the inbox every minute.

A ➕ Create request from the app starts the "Create post" workflow straight away
(the one-time code is checked there). Standard library only: runs before any install.
    python3 inbox_watch.py <seconds>
"""
import json
import subprocess
import sys
import time
import urllib.request

import status

TOPIC = json.load(open("config.json")).get("inbox_topic")
REPO = subprocess.run(["sh", "-c", "echo $GITHUB_REPOSITORY"], capture_output=True, text=True).stdout.strip()


def poll(since: int) -> list[dict]:
    url = f"https://ntfy.sh/{TOPIC}/json?poll=1&since={since}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            lines = r.read().decode().splitlines()
    except Exception as e:
        print("inbox:", e)
        return []
    out = []
    for line in lines:
        try:
            m = json.loads(line)
            out.append({"id": m["id"], **json.loads(m.get("message", "{}"))})
        except (json.JSONDecodeError, KeyError):
            pass
    return out


def already_started() -> set:
    """Requests the robot already reported on (the ⏳ Activity topic): never start them twice."""
    url = f"https://ntfy.sh/{status.TOPIC}/json?poll=1&since=1h"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            lines = r.read().decode().splitlines()
    except Exception:
        return set()
    out = set()
    for line in lines:
        try:
            m = json.loads(json.loads(line).get("message", "{}"))
            if m.get("job") == "create":
                out.add(str(m.get("ref")))
        except (json.JSONDecodeError, AttributeError):
            pass
    return out


def main(wait: int) -> None:
    end = time.time() + wait
    since = int(time.time()) - 600     # also catch requests sent while the last post was being made
    seen = set()
    while time.time() < end:
        for m in poll(since):
            if m["id"] in seen or m.get("type") != "create" or not m.get("text"):
                continue
            seen.add(m["id"])
            if str(m.get("ts", "")) in already_started():
                print(f"Already started earlier, skipped: {m['text'][:60]}")
                continue
            print(f"Create request received: {m['text'][:60]}")
            reel = m.get("format") == "reel"
            status.start("create", m.get("ts", ""), f"✍️ {'🎬 Reel' if reel else '📰 Post'}: {m['text'][:60]}", 6,
                         "Robot received your request")
            subprocess.run(["gh", "workflow", "run", "create-post.yml", "-R", REPO,
                            "-f", f"text={m['text'][:2000]}", "-f", f"otp={m.get('otp', '')}",
                            "-f", f"ts={m.get('ts', 0)}", "-f", f"format={m.get('format', 'post')}",
                            "-f", f"media={json.dumps(m['media']) if m.get('media') else ''}"], check=False)
        time.sleep(max(1, min(60, end - time.time())))


if __name__ == "__main__":
    main(int(sys.argv[1]))
