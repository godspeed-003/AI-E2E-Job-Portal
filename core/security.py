"""Password hashing and token minting.

Uses the standard library's scrypt rather than bcrypt/argon2 on purpose: it is
memory-hard, it is in CPython, and it needs no compiled wheel — one less thing
to fail on a fresh Windows machine.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

# Cost parameters: n=2**15 with r=8 is roughly 32 MB and ~100 ms per hash on a
# laptop — enough to make offline cracking expensive without hurting login UX.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32
_SALT_BYTES = 16

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str) -> str:
    """Return a self-describing hash: ``scrypt$n$r$p$salt$key``."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
        maxmem=64 * 1024 * 1024,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(key)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check that never raises on malformed input."""
    try:
        algo, n_s, r_s, p_s, salt_s, key_s = stored.split("$")
        if algo != "scrypt":
            return False
        expected = _b64d(key_s)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64d(salt_s),
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(expected, actual)


def new_session_token() -> str:
    """Opaque bearer token handed to the browser. 256 bits of entropy."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Sessions are stored hashed, so a leaked database cannot be replayed."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(normalize_email(email)))


def password_problem(password: str) -> str | None:
    """Return a human-readable reason the password is unacceptable, else None."""
    if len(password or "") < 8:
        return "Password must be at least 8 characters."
    if password.lower() in {"password", "12345678", "qwerty123", "letmein1"}:
        return "That password is too common."
    if password.strip() != password:
        return "Password must not start or end with a space."
    return None


def sha256_file_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
