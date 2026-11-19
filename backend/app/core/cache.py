"""线程安全的内存 TTL 缓存。"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Hashable


class TTLCache:
    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._items: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at and expires_at < time.monotonic():
                self._items.pop(key, None)
                return None
            return value

    def set(self, key: Hashable, value: Any, ttl_seconds: float | None) -> None:
        expires_at = time.monotonic() + ttl_seconds if ttl_seconds else 0.0
        with self._lock:
            if len(self._items) >= self._maxsize:
                self._evict_one()
            self._items[key] = (expires_at, value)

    def get_or_set(
        self,
        key: Hashable,
        ttl_seconds: float | None,
        factory: Callable[[], Any],
    ) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = factory()
        self.set(key, value, ttl_seconds)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _evict_one(self) -> None:
        now = time.monotonic()
        expired = [key for key, (exp, _) in self._items.items() if exp and exp < now]
        target = expired[0] if expired else next(iter(self._items))
        self._items.pop(target, None)


cache = TTLCache()


def make_key(*parts: Any) -> tuple[str, ...]:
    return tuple(str(part) for part in parts)
