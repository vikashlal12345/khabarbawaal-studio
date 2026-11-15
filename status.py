"""📡 Progress lines for the app's ⏳ Activity box: each step of a 🎬 Reel or a ➕ Create request is sent
to the private ntfy.sh topic `status_topic`; the app polls it while open. Standard library only (the
inbox watcher uses it before anything is installed). Never fails the caller.

    status.start("reel_clip", "2026-10-10", "🎬 1 PM Reel", steps=6)
    status.step("Finding a Free clip", 4)
    status.done("Ready: <headline>", post="<post id>")   /   status.fail("<reason>")
"""
import json
import time
import urllib.request
from pathlib import Path

TOPIC = json.loads((Path(__file__).parent / "config.json").read_text()).get("status_topic", "")
JOB = {"job": "", "ref": "", "title": "", "of": 0}


def send(**body) -> None:
    if not TOPIC or not JOB["job"]:
        return
    msg = {**JOB, **body, "at": int(time.time())}
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{TOPIC}", data=json.dumps(msg, ensure_ascii=False).encode(),
                                     method="POST")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"  status not sent: {e}")


def start(job: str, ref: str, title: str, steps: int, text: str = "Started") -> None:
    JOB.update(job=job, ref=str(ref), title=title, of=steps)
    send(state="run", n=1, text=text)


def step(text: str, n: int) -> None:
    print(f"  [status] {text}")
    send(state="run", n=n, text=text)


def done(text: str, post: str = "") -> None:
    send(state="ok", n=JOB["of"], text=text, post=post)


def fail(reason: str, fallback: bool = False) -> None:
    """fallback=True: something else was made instead (shown as ⚠️, not ❌)."""
    send(state="fallback" if fallback else "fail", n=JOB["of"], text=reason[:300])
