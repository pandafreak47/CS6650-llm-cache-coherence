from __future__ import annotations

from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Iterable

from .models import KVState

# Null-byte separator avoids collisions between paths that share a prefix.
_SEP = "\x00"


def make_key(files: Iterable[str]) -> str:
    """Canonical cache key for a set of file paths.

    Files are sorted alphabetically so the same set always produces the same
    key regardless of the order they were passed in.
    """
    return _SEP.join(sorted(files))


def _key_to_set(key: str) -> frozenset[str]:
    return frozenset(key.split(_SEP))


class KVCacheInterface(ABC):
    """
    Interface for prefix-cache storage.

    Swap in a Redis-backed implementation by subclassing this — the worker
    and message_builder only depend on this interface.
    """

    @abstractmethod
    def get(self, key: str) -> KVState | None:
        """Return the cached KVState or None. Marks the entry as recently used."""
        ...

    @abstractmethod
    def put(self, key: str, value: KVState) -> None:
        """Store a KVState. Evicts the least-recently-used entry if at capacity."""
        ...

    @abstractmethod
    def find_best_prefix(self, file_set: frozenset[str]) -> tuple[frozenset[str], KVState] | None:
        """
        Find the largest cached subset of file_set.

        Returns (cached_files, KVState) so the caller knows exactly which files
        are already covered and can process only the remainder.
        Returns None on a total miss.
        """
        ...

    @abstractmethod
    def invalidate(self, file_path: str) -> int:
        """
        Remove all entries whose file set contains file_path.

        Called after a successful commit so stale KV states are not reused.
        Returns the number of entries evicted.
        """
        ...


class InMemoryKVCache(KVCacheInterface):
    """LRU in-memory cache with a fixed maximum number of entries."""

    def __init__(self, capacity: int = 100):
        self._capacity = capacity
        self._cache: OrderedDict[str, KVState] = OrderedDict()

    def get(self, key: str) -> KVState | None:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)  # mark as recently used
        return self._cache[key]

    def put(self, key: str, value: KVState) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._capacity:
            self._cache.popitem(last=False)  # evict least-recently-used

    def find_best_prefix(self, file_set: frozenset[str]) -> tuple[frozenset[str], KVState] | None:
        """Scan all entries and return the largest cached subset of file_set."""
        best_key: str | None = None
        best_size: int = 0
        for key in self._cache:
            key_files = _key_to_set(key)
            if key_files.issubset(file_set) and len(key_files) > best_size:
                best_size = len(key_files)
                best_key = key
        if best_key is None:
            return None
        return _key_to_set(best_key), self.get(best_key)

    def invalidate(self, file_path: str) -> int:
        stale = [k for k in self._cache if file_path in _key_to_set(k)]
        for k in stale:
            del self._cache[k]
        return len(stale)
