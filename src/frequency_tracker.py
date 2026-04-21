from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Iterable

_REDIS_FREQ_KEY = "cs6650:freq"


class FrequencyTrackerInterface(ABC):
    @abstractmethod
    def update(self, files: Iterable[str]) -> None:
        """Increment the count for each file by 1."""

    @abstractmethod
    def get(self, files: Iterable[str]) -> dict[str, int]:
        """Return {file: count} for the given files. Missing files return 0."""

    @abstractmethod
    def clear(self) -> None:
        """Reset all counts to zero."""


class InMemoryFrequencyTracker(FrequencyTrackerInterface):
    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)

    def update(self, files: Iterable[str]) -> None:
        for f in files:
            self._counts[f] += 1

    def get(self, files: Iterable[str]) -> dict[str, int]:
        return {f: self._counts[f] for f in files}

    def clear(self) -> None:
        self._counts.clear()


class RedisFrequencyTracker(FrequencyTrackerInterface):
    def __init__(self, redis_url: str) -> None:
        import redis as _redis
        self._r = _redis.from_url(redis_url)

    def update(self, files: Iterable[str]) -> None:
        file_list = list(files)
        if not file_list:
            return
        pipe = self._r.pipeline()
        for f in file_list:
            pipe.hincrby(_REDIS_FREQ_KEY, f, 1)
        pipe.execute()

    def get(self, files: Iterable[str]) -> dict[str, int]:
        file_list = list(files)
        if not file_list:
            return {}
        counts = self._r.hmget(_REDIS_FREQ_KEY, file_list)
        return {f: int(c or 0) for f, c in zip(file_list, counts)}

    def clear(self) -> None:
        self._r.delete(_REDIS_FREQ_KEY)


def make_frequency_tracker(backend: str, redis_url: str = "") -> FrequencyTrackerInterface:
    if backend == "redis":
        return RedisFrequencyTracker(redis_url)
    return InMemoryFrequencyTracker()
