from __future__ import annotations

import base64
import json
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Iterable

from .models import AnthropicCachedState, LLMState, LlamaKVState

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


@dataclass
class CacheStats:
    """I/O accounting for the LLM state cache."""
    bytes_written: int = 0  # total bytes put into cache
    bytes_read: int = 0     # total bytes retrieved on cache hits
    hit_count: int = 0      # find_best_prefix returned a result
    miss_count: int = 0     # find_best_prefix returned None


class KVCacheInterface(ABC):
    """
    Interface for prefix-cache storage.

    Swap in a Redis-backed implementation by subclassing this — the worker
    and message_builder only depend on this interface.
    """

    @abstractmethod
    def get(self, key: str) -> LLMState | None:
        """Return the cached LLMState or None. Marks the entry as recently used."""
        ...

    @abstractmethod
    def put(self, key: str, value: LLMState) -> None:
        """Store a LLMState. Evicts the least-recently-used entry if at capacity."""
        ...

    @abstractmethod
    def find_best_prefix(self, file_set: frozenset[str]) -> tuple[frozenset[str], LLMState] | None:
        """
        Find the largest cached subset of file_set.

        Returns (cached_files, LLMState) so the caller knows exactly which files
        are already covered and can process only the remainder.
        Returns None on a total miss.
        """
        ...

    @abstractmethod
    def invalidate(self, file_path: str) -> int:
        """
        Remove all entries whose file set contains file_path.

        Called after a successful commit so stale states are not reused.
        Returns the number of entries evicted.
        """
        ...

    @abstractmethod
    def stats(self) -> CacheStats:
        """Return cumulative I/O accounting since last reset."""
        ...

    @abstractmethod
    def reset_stats(self) -> None:
        """Zero all I/O counters."""
        ...


# ---------------------------------------------------------------------------
# Redis serialization helpers
# ---------------------------------------------------------------------------

_IDX_KEY = "cs6650:idx"
_LRU_KEY = "cs6650:lru"  # sorted set: score=timestamp, member=cache key


def _rkey(key: str) -> str:
    return "cs6650:state:" + base64.urlsafe_b64encode(key.encode()).decode()


def _serialize(state: LLMState) -> str:
    d = json.loads(state.model_dump_json())
    d["__type__"] = type(state).__name__
    return json.dumps(d)


def _deserialize(blob: str) -> LLMState:
    d = json.loads(blob)
    t = d.pop("__type__", "LLMState")
    if t == "AnthropicCachedState":
        return AnthropicCachedState.model_validate(d)
    if t == "LlamaKVState":
        return LlamaKVState.model_validate(d)
    return LLMState.model_validate(d)


# ---------------------------------------------------------------------------
# Redis-backed implementation
# ---------------------------------------------------------------------------

class RedisKVCache(KVCacheInterface):
    """Shared prefix-cache backed by Redis. Cross-pod reads/writes via ElastiCache."""

    def __init__(self, redis_url: str, capacity: int = 100):
        import redis as redis_lib  # noqa: PLC0415
        self._r = redis_lib.from_url(redis_url, decode_responses=True)
        self._capacity = capacity
        self._stats = CacheStats()

    def get(self, key: str) -> LLMState | None:
        blob = self._r.get(_rkey(key))
        return _deserialize(blob) if blob else None

    def put(self, key: str, value: LLMState) -> None:
        self._r.set(_rkey(key), _serialize(value))
        self._r.sadd(_IDX_KEY, key)
        self._r.zadd(_LRU_KEY, {key: time.time()})
        self._stats.bytes_written += value.byte_size()
        # Evict least-recently-used entries beyond capacity
        overflow = self._r.zcard(_LRU_KEY) - self._capacity
        if overflow > 0:
            evicted = self._r.zpopmin(_LRU_KEY, overflow)
            for evict_key, _ in evicted:
                self._r.delete(_rkey(evict_key))
                self._r.srem(_IDX_KEY, evict_key)

    def find_best_prefix(self, file_set: frozenset[str]) -> tuple[frozenset[str], LLMState] | None:
        all_keys = self._r.smembers(_IDX_KEY)
        best_key: str | None = None
        best_size = 0
        for k in all_keys:
            kfiles = frozenset(k.split(_SEP))
            if kfiles.issubset(file_set) and len(kfiles) > best_size:
                best_size, best_key = len(kfiles), k
        if best_key is None:
            self._stats.miss_count += 1
            return None
        blob = self._r.get(_rkey(best_key))
        if blob is None:  # evicted by Redis maxmemory policy
            self._r.srem(_IDX_KEY, best_key)
            self._stats.miss_count += 1
            return None
        state = _deserialize(blob)
        self._r.zadd(_LRU_KEY, {best_key: time.time()})  # refresh LRU timestamp on hit
        self._stats.hit_count += 1
        self._stats.bytes_read += state.byte_size()
        return frozenset(best_key.split(_SEP)), state

    def invalidate(self, file_path: str) -> int:
        all_keys = self._r.smembers(_IDX_KEY)
        stale = [k for k in all_keys if file_path in k.split(_SEP)]
        for k in stale:
            self._r.delete(_rkey(k))
            self._r.srem(_IDX_KEY, k)
            self._r.zrem(_LRU_KEY, k)
        return len(stale)

    def stats(self) -> CacheStats:
        return CacheStats(
            bytes_written=self._stats.bytes_written,
            bytes_read=self._stats.bytes_read,
            hit_count=self._stats.hit_count,
            miss_count=self._stats.miss_count,
        )

    def reset_stats(self) -> None:
        self._stats = CacheStats()


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

class InMemoryKVCache(KVCacheInterface):
    """LRU in-memory cache with a fixed maximum number of entries."""

    def __init__(self, capacity: int = 100):
        self._capacity = capacity
        self._cache: OrderedDict[str, LLMState] = OrderedDict()
        self._stats = CacheStats()

    def get(self, key: str) -> LLMState | None:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: str, value: LLMState) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        self._stats.bytes_written += value.byte_size()
        if len(self._cache) > self._capacity:
            self._cache.popitem(last=False)

    def find_best_prefix(self, file_set: frozenset[str]) -> tuple[frozenset[str], LLMState] | None:
        """Scan all entries and return the largest cached subset of file_set."""
        best_key: str | None = None
        best_size: int = 0
        for key in self._cache:
            key_files = _key_to_set(key)
            if key_files.issubset(file_set) and len(key_files) > best_size:
                best_size = len(key_files)
                best_key = key
        if best_key is None:
            self._stats.miss_count += 1
            return None
        state = self.get(best_key)
        self._stats.hit_count += 1
        self._stats.bytes_read += state.byte_size()
        return _key_to_set(best_key), state

    def invalidate(self, file_path: str) -> int:
        stale = [k for k in self._cache if file_path in _key_to_set(k)]
        for k in stale:
            del self._cache[k]
        return len(stale)

    def stats(self) -> CacheStats:
        return CacheStats(
            bytes_written=self._stats.bytes_written,
            bytes_read=self._stats.bytes_read,
            hit_count=self._stats.hit_count,
            miss_count=self._stats.miss_count,
        )

    def reset_stats(self) -> None:
        self._stats = CacheStats()
