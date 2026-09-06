"""Password hashing and session cookies, in the standard library.

`passlib[bcrypt]` is the usual answer and would be fine. It is not used here
because `requirements.txt` says out loud that it is kept to six pure-Python
packages so the image stays small, and bcrypt is a native build. `scrypt` has
been in `hashlib` since 3.6, is memory-hard, and is a sound choice for this;
taking the dependency would buy familiarity rather than security.

Everything here is small enough to read in one sitting, which is the point. The
failure this guards against is not a clever attack on the KDF -- it is a bar
screen on a public URL with no login at all.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

__all__ = [
    "hash_password",
    "verify_password",
    "sign_session",
    "read_session",
    "session_key",
    "SESSION_COOKIE",
    "SESSION_MAX_AGE_S",
]

log = logging.getLogger("cafe.security")

SESSION_COOKIE = "cafe_session"
#: A shift is eight hours; a session that outlives the day it was opened on is a
#: laptop left logged in behind a counter.
SESSION_MAX_AGE_S = 12 * 60 * 60

#: scrypt at the parameters the docs suggest for interactive logins. n is the
#: cost; raising it is a one-line change and old hashes keep working, because
#: every stored hash carries the parameters it was made with.
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32

_key: bytes | None = None


def session_key() -> bytes:
    """The secret sessions are signed with.

    From `CAFE_SECRET_KEY`. Without it a random one is generated and every
    session dies at the next restart -- which is survivable for a demo and said
    out loud rather than defaulted to something predictable, because a signing
    key with a default is not a signing key.
    """
    global _key
    if _key is not None:
        return _key
    given = os.environ.get("CAFE_SECRET_KEY")
    if given:
        _key = given.encode()
    else:
        _key = secrets.token_bytes(32)
        log.warning(
            "no CAFE_SECRET_KEY: sessions are signed with a key that is thrown "
            "away at restart, so every login ends when this process does"
        )
    return _key


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(plain: str) -> str:
    """`scrypt$n$r$p$salt$hash`, carrying its own parameters.

    Stored with the cost it was made at, so raising the cost later does not
    invalidate anybody's password.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(plain.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(plain: str, stored: str) -> bool:
    """Constant-time, and false rather than raising on anything malformed."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        want = hashlib.scrypt(
            plain.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=_DKLEN
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(want, _unb64(digest))


def sign_session(username: str, *, issued_at: float | None = None) -> str:
    """`username.issued.signature`, signed over both."""
    issued = int(issued_at if issued_at is not None else time.time())
    body = f"{_b64(username.encode())}.{issued}"
    mac = hmac.new(session_key(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def read_session(cookie: str | None, *, now: float | None = None) -> str | None:
    """The username a cookie proves, or None.

    None for anything wrong: no cookie, a bad signature, an expired one, or a
    shape that does not parse. The caller has one question and gets one answer,
    so there is nowhere to leak which of those it was.
    """
    if not cookie:
        return None
    try:
        name, issued, mac = cookie.rsplit(".", 2)
        body = f"{name}.{issued}"
        want = hmac.new(session_key(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(mac)):
            return None
        age = (now if now is not None else time.time()) - int(issued)
        if age < 0 or age > SESSION_MAX_AGE_S:
            return None
        return _unb64(name).decode()
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
