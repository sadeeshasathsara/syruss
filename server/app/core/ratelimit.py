"""
A small sliding-window limiter for the few unauthenticated or account-creating routes. It counts per
process; put a shared limiter (or the gateway's) in front when running several API processes.
"""
import time
from collections import defaultdict, deque
from fastapi import HTTPException

_hits = defaultdict(deque)
_windows = {}           # key -> its window in seconds, so a sweep knows when a key's hits have all expired
_SWEEP_EVERY = 300      # seconds between sweeps of keys nobody has used for a whole window
_last_sweep = time.monotonic()


def _sweep(now: float) -> None:
    """Forgets keys whose hits have all expired: one key per address or account would otherwise stay forever."""
    global _last_sweep
    _last_sweep = now
    for key in [k for k, hits in _hits.items() if not hits or now - hits[-1] > _windows.get(k, 0)]:
        _hits.pop(key, None)
        _windows.pop(key, None)


def limit(key: str, max_hits: int, per_seconds: int) -> None:
    now = time.monotonic()
    if now - _last_sweep > _SWEEP_EVERY:
        _sweep(now)
    window = _hits[key]
    _windows[key] = max(_windows.get(key, 0), per_seconds)
    while window and now - window[0] > per_seconds:
        window.popleft()
    if len(window) >= max_hits:
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a moment and try again.",
                            headers={"Retry-After": str(per_seconds)})
    window.append(now)


def reset() -> None:
    _hits.clear()
    _windows.clear()
