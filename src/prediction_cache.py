from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Protocol

from cachetools import LRUCache

try:
    from src.models import PredictionResponse
except ModuleNotFoundError:
    from models import PredictionResponse

logger = logging.getLogger(__name__)


class Predictor(Protocol):
    def predict(self, match_id: str) -> PredictionResponse:
        ...


@dataclass
class CacheEntry:
    value: PredictionResponse
    expires_at_monotonic: float
    refresh_in_flight: bool = False
    last_refresh_attempt_at: float | None = None
    last_refresh_error: str | None = None


class PredictionCacheService:
    def __init__(
        self,
        predictor: Predictor,
        ttl_seconds: float = 900.0,
        maxsize: int = 512,
        refresh_retry_cooldown_seconds: float = 30.0,
        max_refresh_workers: int = 2,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.predictor = predictor
        self.ttl_seconds = ttl_seconds
        self.refresh_retry_cooldown_seconds = refresh_retry_cooldown_seconds
        self.clock = clock or time.monotonic

        self._cache: LRUCache[str, CacheEntry] = LRUCache(maxsize=maxsize)
        self._cache_lock = threading.RLock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._refresh_executor = ThreadPoolExecutor(
            max_workers=max_refresh_workers,
            thread_name_prefix="prediction-cache-refresh",
        )

    def get_prediction(self, match_id: str) -> PredictionResponse:
        now = self.clock()
        with self._cache_lock:
            entry = self._cache.get(match_id)
            if entry is not None:
                if entry.expires_at_monotonic > now:
                    logger.info("cache_hit_fresh match_id=%s", match_id)
                    return entry.value
                self._maybe_schedule_refresh_locked(match_id=match_id, entry=entry, now=now)
                logger.info("cache_hit_stale match_id=%s", match_id)
                return entry.value

        logger.info("cache_miss match_id=%s", match_id)
        lock = self._get_key_lock(match_id)
        with lock:
            now = self.clock()
            with self._cache_lock:
                entry = self._cache.get(match_id)
                if entry is not None:
                    if entry.expires_at_monotonic > now:
                        logger.info("cache_hit_fresh_after_wait match_id=%s", match_id)
                        return entry.value
                    self._maybe_schedule_refresh_locked(match_id=match_id, entry=entry, now=now)
                    logger.info("cache_hit_stale_after_wait match_id=%s", match_id)
                    return entry.value

            value = self.predictor.predict(match_id)
            now = self.clock()
            with self._cache_lock:
                self._cache[match_id] = CacheEntry(
                    value=value,
                    expires_at_monotonic=now + self.ttl_seconds,
                )
            logger.info("cache_store match_id=%s ttl_seconds=%s", match_id, self.ttl_seconds)
            return value

    def clear(self) -> None:
        """Drop all cached predictions after external result state changes."""
        with self._cache_lock:
            self._cache.clear()
            self._key_locks.clear()

    def _get_key_lock(self, match_id: str) -> threading.Lock:
        with self._cache_lock:
            lock = self._key_locks.get(match_id)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[match_id] = lock
            return lock

    def _maybe_schedule_refresh_locked(self, match_id: str, entry: CacheEntry, now: float) -> None:
        if entry.refresh_in_flight:
            return

        if (
            entry.last_refresh_attempt_at is not None
            and (now - entry.last_refresh_attempt_at) < self.refresh_retry_cooldown_seconds
        ):
            return

        entry.refresh_in_flight = True
        entry.last_refresh_attempt_at = now
        entry.last_refresh_error = None
        future = self._refresh_executor.submit(self._refresh_cache_entry, match_id)
        future.add_done_callback(lambda done: self._on_refresh_done(match_id=match_id, future=done))
        logger.info("cache_refresh_started match_id=%s", match_id)

    def _refresh_cache_entry(self, match_id: str) -> PredictionResponse:
        return self.predictor.predict(match_id)

    def _on_refresh_done(self, match_id: str, future: Future[PredictionResponse]) -> None:
        now = self.clock()
        with self._cache_lock:
            entry = self._cache.get(match_id)
            if entry is None:
                return

            try:
                value = future.result()
            except Exception as exc:
                entry.refresh_in_flight = False
                entry.last_refresh_error = str(exc)
                logger.exception("cache_refresh_failed match_id=%s", match_id)
                return

            entry.value = value
            entry.expires_at_monotonic = now + self.ttl_seconds
            entry.refresh_in_flight = False
            entry.last_refresh_error = None
            logger.info("cache_refresh_succeeded match_id=%s ttl_seconds=%s", match_id, self.ttl_seconds)
