"""
Process-wide rotating Claude API key pool.

The point: spread load evenly across however many Anthropic keys are
configured, so no single key bears the whole audit/fix workload and a
rate-limited key can be rotated off mid-flight.

Rotation strategy ("shuffle at each run"):
  - On first use the configured keys (settings.anthropic_keys()) are loaded
    and SHUFFLED ONCE per process start. Shuffling means two processes that
    share the same key list won't both hammer key[0] first — the starting
    offset is randomized per process.
  - next_key() then walks the shuffled list round-robin (thread-safe via a
    Lock), so within a process the load is spread evenly and deterministically
    after the initial shuffle.

If no keys are configured, next_key() returns None and every caller degrades
gracefully (no Claude calls, no Claude-in-sandbox) — the app still boots and
audits still run.
"""
from __future__ import annotations

import random
import threading

from ..config import settings

_lock = threading.Lock()
_keys: list[str] | None = None  # None = not yet loaded
_cursor = 0


def _ensure_loaded() -> None:
    """Load + shuffle the key pool exactly once per process (lock held)."""
    global _keys
    if _keys is None:
        loaded = list(settings.anthropic_keys())
        random.shuffle(loaded)  # randomize order once at first use
        _keys = loaded


def next_key() -> str | None:
    """Return the next key in round-robin order, or None if the pool is empty."""
    global _cursor
    with _lock:
        _ensure_loaded()
        assert _keys is not None  # set by _ensure_loaded
        if not _keys:
            return None
        key = _keys[_cursor % len(_keys)]
        _cursor += 1
        return key


def has_keys() -> bool:
    """True if at least one Claude key is configured."""
    with _lock:
        _ensure_loaded()
        assert _keys is not None
        return len(_keys) > 0


def key_count() -> int:
    """Number of keys in the pool."""
    with _lock:
        _ensure_loaded()
        assert _keys is not None
        return len(_keys)
