"""Renew the Claude membership token used by the hourly robot.

Runs `claude setup-token` in a pseudo-terminal (the browser opens for sign-in; if it
asks for a code, write it to code.txt next to this script), tests the new token on
its own, then saves it as the CLAUDE_CODE_OAUTH_TOKEN GitHub secret. The token is
never printed. Afterwards update membership_token_created in config.json.

    python3 renew_token.py
"""
import fcntl, os, pty, re, select, struct, subprocess, sys, termios, time

SP = os.path.dirname(os.path.abspath(__file__))
import glob, shutil
# Claude Code CLI: on PATH, or the copy bundled with the VS Code extension (newest).
CLAUDE = shutil.which("claude") or sorted(glob.glob(os.path.expanduser(
    "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude")))[-1]
CODE_FILE = os.path.join(SP, "code.txt")
LOG = os.path.join(SP, ".renew_token.log")  # safe log: token redacted
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[a-zA-Z]")

def log(msg):
    with open(LOG, "a") as f:
        f.write(msg + "\n")

pid, fd = pty.fork()
if pid == 0:
    os.execv(CLAUDE, [CLAUDE, "setup-token"])
# Very wide terminal so the token prints on one line, never wrapped.
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 1000, 0, 0))

buf, sent, deadline = "", False, time.time() + 1100
while time.time() < deadline:
    r, _, _ = select.select([fd], [], [], 1)
    if r:
        try:
            chunk = os.read(fd, 65536).decode(errors="ignore")
        except OSError:
            break
        buf += chunk
        clean = ANSI.sub(" ", chunk)
        log(re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "[TOKEN]", clean))
    if not sent and os.path.exists(CODE_FILE):
        code = open(CODE_FILE).read().strip()
        os.remove(CODE_FILE)
        os.write(fd, (code + "\r").encode())
        sent = True
        log("[helper] code sent")
    if re.search(r"sk-ant-oat[A-Za-z0-9_\-]{20,}[\s\x1b]", ANSI.sub(" ", buf)):
        time.sleep(1.5)
        try:
            while select.select([fd], [], [], 0.5)[0]:
                buf += os.read(fd, 65536).decode(errors="ignore")
        except OSError:
            pass
        break

tokens = re.findall(r"sk-ant-oat[A-Za-z0-9_\-]{20,}", ANSI.sub(" ", buf))
m = max(tokens, key=len, default=None)
if m:
    log(f"[helper] token length {len(m)}")
    m = re.match(r".*", m)
if not m:
    log("[helper] no token found")
    sys.exit(1)
# Prove the token works on its own (fresh HOME, so the Mac's own login can't help).
import tempfile
env = dict(os.environ, CLAUDE_CODE_OAUTH_TOKEN=m.group(0), HOME=tempfile.mkdtemp())
env.pop("ANTHROPIC_API_KEY", None)
test = subprocess.run([CLAUDE, "-p", "Reply with just: OK", "--tools", "", "--model", "sonnet"],
                      env=env, capture_output=True, text=True, timeout=180)
log(f"[helper] token test exit={test.returncode} out={test.stdout.strip()[:40]!r} err={test.stderr.strip()[:120]!r}")
if test.returncode != 0:
    sys.exit(2)
res = subprocess.run(["gh", "secret", "set", "CLAUDE_CODE_OAUTH_TOKEN",
                      "-R", "vikashlal12345/khabarbawaal-studio"],
                     input=m.group(0), text=True, capture_output=True)
log(f"[helper] gh secret set exit={res.returncode} {res.stderr.strip()[:200]}")
sys.exit(res.returncode)
