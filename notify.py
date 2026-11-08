"""Turn .alerts.json (written by generate.py) into GitHub issues.

An issue that @mentions the repo owner makes GitHub send them an email. One open
issue per alert type: repeats don't spam, and resolved alerts close themselves.
Runs inside GitHub Actions with GH_TOKEN set.  `python notify.py --test` sends a test alert.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ALERTS_FILE = Path(os.environ.get("ALERTS_FILE", Path(__file__).parent / ".alerts.json"))
OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", "vikashlal12345")
LABEL = "alert"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def open_alerts() -> dict:
    issues = json.loads(gh("issue", "list", "--label", LABEL, "--state", "open",
                           "--json", "number,title", "--limit", "100"))
    # Titles look like "[token] ...": key -> issue number.
    return {i["title"].split("]")[0].lstrip("⚠️ ["): i["number"] for i in issues if "]" in i["title"]}


def main() -> int:
    if "--test" in sys.argv:
        alerts = {"raise": [{"key": "test", "title": "Test alert: email notifications work",
                             "body": "If you got this by email, KhabarBawaal alerts are set up correctly. "
                                     "You can close this issue."}], "resolve": []}
    elif ALERTS_FILE.exists():
        alerts = json.loads(ALERTS_FILE.read_text())
    else:
        return 0

    gh("label", "create", LABEL, "--color", "E50914", "--description", "KhabarBawaal robot alert", "--force")
    existing = open_alerts()

    for a in alerts["raise"]:
        if a["key"] in existing:
            print(f"[{a['key']}] already open (#{existing[a['key']]}), not repeating")
            continue
        body = f"@{OWNER}\n\n{a['body']}\n\n---\n_Sent automatically by the KhabarBawaal hourly robot._"
        url = gh("issue", "create", "--title", f"⚠️ [{a['key']}] {a['title']}", "--body", body, "--label", LABEL)
        print(f"[{a['key']}] opened {url.strip()}")

    raised = {a["key"] for a in alerts["raise"]}
    for key in alerts["resolve"]:
        if key in existing and key not in raised:
            gh("issue", "close", str(existing[key]), "--comment", "✅ Fixed: working normally again. Closed automatically.")
            print(f"[{key}] resolved, closed #{existing[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
