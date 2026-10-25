"""
A small sliding-window limiter for the few unauthenticated or account-creating routes. It counts per
process; put a shared limiter (or the gateway's) in front when running several API processes.
"""
import time
from collections import defaultdict, deque
from fastapi import HTTPException

_hits = defaultdict(deque)


def limit(key: str, max_hits: int, per_seconds: int) -> None:
    now = time.monotonic()
    window = _hits[key]
    while window and now - window[0] > per_seconds:
        window.popleft()
    if len(window) >= max_hits:
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a moment and try again.",
                            headers={"Retry-After": str(per_seconds)})
    window.append(now)


def reset() -> None:
    _hits.clear()
