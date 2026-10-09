"""Password login for serving the UI on the local network.

- The password is stored only as a salted scrypt hash (``web.password_hash``).
- A successful login sets an HMAC-signed, HttpOnly session cookie. The
  signing secret lives in a file next to the database (created on first use,
  readable only by you), so cookies survive restarts but not a new secret.
"""

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
COOKIE = "litresearch_session"
SESSION_DAYS = 30


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Use at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of a password against a stored scrypt hash."""
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def load_secret(path: Path) -> bytes:
    """Read (or create) the cookie signing secret."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_hex(32))
    return bytes.fromhex(path.read_text().strip())


def make_session(secret: bytes, password_hash: str, now: float | None = None) -> str:
    """Session token: expiry + signature.

    The password hash is part of the signed message, so changing the
    password logs out all existing sessions.
    """
    expires = int((now or time.time()) + SESSION_DAYS * 86400)
    return f"{expires}.{_sign(secret, password_hash, expires)}"


def check_session(token: str | None, secret: bytes, password_hash: str, now=None) -> bool:
    try:
        expires_text, signature = (token or "").split(".", 1)
        expires = int(expires_text)
    except ValueError:
        return False
    if expires < (now or time.time()):
        return False
    return hmac.compare_digest(signature, _sign(secret, password_hash, expires))


def _sign(secret: bytes, password_hash: str, expires: int) -> str:
    message = f"{expires}:{password_hash}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def sign_value(secret: bytes, value: str) -> str:
    """HMAC signature for small cookie values (e.g. the chosen profile)."""
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()[:32]
