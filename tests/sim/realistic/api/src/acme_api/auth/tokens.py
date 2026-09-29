"""Bearer tokens: HMAC-signed, with an expiry."""

import hashlib
import hmac
import time

SECRET = b"change-me"


def issue_token(user_id: int, ttl_seconds: int = 3600) -> str:
    expires = int(time.time()) + ttl_seconds
    payload = f"{user_id}:{expires}"
    sig = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_token(token: str) -> int | None:
    """Return the user id if the token is valid and not expired, else None."""
    try:
        user_id, expires, sig = token.split(":")
    except ValueError:
        return None
    expected = hmac.new(SECRET, f"{user_id}:{expires}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected) or int(expires) < time.time():
        return None
    return int(user_id)
