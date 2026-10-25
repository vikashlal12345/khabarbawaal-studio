"""Time-based one-time codes (TOTP, RFC 6238), the 6-digit codes from Google Authenticator.

➕ Create requests sent through the app's inbox must carry a valid code from the owner's
authenticator app. A code is accepted only once and only near the time it was made.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time


def code_at(secret: str, t: float, step: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", int(t // step)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def verify(secret: str, code: str, sent_at: float, used: list[str], max_age: int = 900) -> tuple[bool, str]:
    """Code must match the time the request was sent (±60 s for phone clock drift), the request must be
    under 15 min old (the robot picks requests up within a few minutes), and the code unused."""
    code = (code or "").strip()
    now = time.time()
    if not (code.isdigit() and len(code) == 6):
        return False, "code must be 6 digits"
    if not (now - max_age <= sent_at <= now + 120):
        return False, "request too old"
    if code + ":" + str(int(sent_at // 30)) in used or any(u.startswith(code + ":") and abs(int(u.split(":")[1]) - int(sent_at // 30)) <= 2 for u in used):
        return False, "code already used"
    if not any(hmac.compare_digest(code_at(secret, sent_at + d), code) for d in (-60, -30, 0, 30, 60)):
        return False, "wrong code"
    return True, "ok"
