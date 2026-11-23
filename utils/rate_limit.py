"""Small in-process rate limiter (security review 2026-11).

Counts hits per key in a sliding window, per worker process. Passenger runs a
few processes, so a limit of N is at most N x processes: a brake on abuse
(brute force, LLM cost, email relays), not an exact quota.

client_ip() trusts X-Forwarded-For only from a local proxy.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_hits: dict = {}
_MAX_KEYS = 50000


def client_ip() -> str:
    """The address to count requests by. The client controls X-Forwarded-For,
    so it is read only when the connection comes from a local proxy (private
    or loopback remote_addr), and then only its rightmost hop, the one that
    proxy appended (same rule as startup_page_service._client_ip)."""
    import ipaddress
    from flask import request
    remote = (request.remote_addr or '').strip()
    fwd = request.headers.get('X-Forwarded-For') or ''
    private = True
    if remote:
        try:
            ip = ipaddress.ip_address(remote)
            private = ip.is_private or ip.is_loopback or ip.is_link_local
        except ValueError:
            private = False
    if fwd and private:
        hops = [h.strip() for h in fwd.split(',') if h.strip()]
        if hops:
            return hops[-1][:45]
    return remote[:45]


def hit(key: str, limit: int, window_seconds: int) -> bool:
    """Record one hit for key. True when the key is over its limit (the hit is
    not counted then)."""
    try:
        from flask import current_app
        if not current_app.config.get('RATE_LIMITS_ENABLED', True):
            return False
    except RuntimeError:
        pass                     # outside an app context: limit as usual
    now = time.monotonic()
    with _lock:
        if len(_hits) > _MAX_KEYS:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 3600]:
                _hits.pop(k, None)
        recent = [t for t in _hits.get(key, ()) if now - t < window_seconds]
        if len(recent) >= limit:
            _hits[key] = recent
            return True
        recent.append(now)
        _hits[key] = recent
        return False


def reset() -> None:
    """Tests only."""
    with _lock:
        _hits.clear()
