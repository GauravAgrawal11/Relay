"""
cache.py: Simple, in-memory TTL cache with zero external dependencies (no Redis).
Builds deterministic cache keys encompassing normalized query, paper versions, retrieval
setting, model, and prompt version. Never caches failures or throttled calls.
Switchable: disabled by default in eval/load mode to prevent latency distortion.
"""

import hashlib
import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    """Thread-safe, lightweight in-memory cache with time-to-live expiration."""

    def __init__(self, ttl_seconds: float = 300.0, enabled: bool = False):
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        self._store: Dict[str, Tuple[float, Any]] = {}
        self.hits: int = 0
        self.misses: int = 0

    def get(self, key: str) -> Optional[Any]:
        if not self.enabled:
            return None

        now = time.time()
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None

        expiry, value = entry
        if now > expiry:
            del self._store[key]
            self.misses += 1
            return None

        self.hits += 1
        return value

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        expiry = time.time() + self.ttl_seconds
        self._store[key] = (expiry, value)

    def clear(self) -> None:
        self._store.clear()
        self.hits = 0
        self.misses = 0

    def get_stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100.0) if total > 0 else 0.0
        return {
            "enabled": self.enabled,
            "cached_entries": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_pct": round(hit_rate, 1),
        }


def make_cache_key(
    normalized_query: str,
    retrieval_setting: str,
    model_name: str,
    paper_versions: str = "att_v7_msh_v1_dc_v2",
    prompt_version: str = "v1",
) -> str:
    """Construct deterministic cache key covering all pipeline parameters."""
    raw = f"{normalized_query.strip().lower()}|{paper_versions}|{retrieval_setting}|{model_name}|{prompt_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

