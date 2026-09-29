"""A tiny read-through cache decorator (in-process stand-in for Redis)."""

import time

_STORE: dict[str, tuple[float, object]] = {}


def cached(ttl_seconds: int):
    def deco(fn):
        def wrapped(request: dict):
            key = f"{fn.__name__}:{request.get('user_id')}"
            hit = _STORE.get(key)
            if hit and hit[0] > time.time():
                return hit[1]
            value = fn(request)
            _STORE[key] = (time.time() + ttl_seconds, value)
            return value

        return wrapped

    return deco
