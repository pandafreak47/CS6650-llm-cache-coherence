from __future__ import annotations

from abc import ABC, abstractmethod
from collections import OrderedDict

from .models import KVState

# Null-byte separator avoids collisions between paths that share a prefix.
_SEP = "\x00"


def make_key(ordered_files: list[str]) -> str:
    """Canonical cache key for an ordered list of processed file paths."""
    return _SEP.join(ordered_files)


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
    def find_best_prefix(self, ordered_files: list[str]) -> tuple[int, KVState] | None:
        """
        Given an ordered list of context files (the intended processing order),
        find the longest cached prefix and return (prefix_length, KVState).
        Returns None on a total miss.
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

    def find_best_prefix(self, ordered_files: list[str]) -> tuple[int, KVState] | None:
        """Try the longest prefix first, working backwards to length 1."""
        for length in range(len(ordered_files), 0, -1):
            key = make_key(ordered_files[:length])
            state = self.get(key)
            if state is not None:
                return length, state
        return None
