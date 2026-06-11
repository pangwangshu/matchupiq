from __future__ import annotations

import threading
import time

from src.models import MatchupCandidate, PredictionResponse
from src.prediction_cache import PredictionCacheService


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._value = start
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._value += seconds


class StubPredictor:
    def __init__(
        self,
        delay_seconds: float = 0.0,
        fail_on_calls: set[int] | None = None,
        block_on_calls: dict[int, threading.Event] | None = None,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.fail_on_calls = fail_on_calls or set()
        self.block_on_calls = block_on_calls or {}
        self._lock = threading.Lock()
        self.call_count = 0

    def predict(self, match_id: str) -> PredictionResponse:
        with self._lock:
            self.call_count += 1
            call_number = self.call_count

        gate = self.block_on_calls.get(call_number)
        if gate is not None:
            gate.wait(timeout=2.0)

        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        if call_number in self.fail_on_calls:
            raise RuntimeError(f"refresh failed on call {call_number}")

        return PredictionResponse(
            match_id=match_id,
            status="predicted",
            top_candidates=[
                MatchupCandidate(
                    home_team=f"Home-{call_number}",
                    away_team=f"Away-{call_number}",
                    score=float(call_number),
                    reason="stub",
                )
            ],
        )


def _wait_until(predicate, timeout_seconds: float = 2.0) -> bool:
    start = time.perf_counter()
    while (time.perf_counter() - start) < timeout_seconds:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_cache_miss_then_fresh_hit_reuses_cached_value() -> None:
    clock = FakeClock(start=100.0)
    predictor = StubPredictor()
    service = PredictionCacheService(
        predictor=predictor,
        ttl_seconds=900.0,
        clock=clock.now,
        max_refresh_workers=1,
    )

    first = service.get_prediction("82")
    second = service.get_prediction("82")

    assert predictor.call_count == 1
    assert first.top_candidates[0].score == 1.0
    assert second.top_candidates[0].score == 1.0


def test_clear_drops_cached_predictions() -> None:
    clock = FakeClock(start=100.0)
    predictor = StubPredictor()
    service = PredictionCacheService(
        predictor=predictor,
        ttl_seconds=900.0,
        clock=clock.now,
        max_refresh_workers=1,
    )

    first = service.get_prediction("82")
    service.clear()
    second = service.get_prediction("82")

    assert predictor.call_count == 2
    assert first.top_candidates[0].score == 1.0
    assert second.top_candidates[0].score == 2.0


def test_expired_hit_returns_stale_and_refreshes_in_background() -> None:
    clock = FakeClock(start=100.0)
    release_refresh = threading.Event()
    predictor = StubPredictor(block_on_calls={2: release_refresh})
    service = PredictionCacheService(
        predictor=predictor,
        ttl_seconds=10.0,
        refresh_retry_cooldown_seconds=1.0,
        clock=clock.now,
        max_refresh_workers=1,
    )

    first = service.get_prediction("82")
    with service._cache_lock:
        old_expiry = service._cache["82"].expires_at_monotonic
    assert first.top_candidates[0].score == 1.0

    clock.advance(11.0)
    started_at = time.perf_counter()
    stale = service.get_prediction("82")
    elapsed = time.perf_counter() - started_at

    assert stale.top_candidates[0].score == 1.0
    assert elapsed < 0.2
    assert _wait_until(lambda: predictor.call_count >= 2)

    release_refresh.set()
    assert _wait_until(
        lambda: service._cache["82"].value.top_candidates[0].score == 2.0
    )
    with service._cache_lock:
        assert service._cache["82"].expires_at_monotonic > old_expiry


def test_cache_miss_is_singleflight_under_concurrency() -> None:
    clock = FakeClock(start=100.0)
    predictor = StubPredictor(delay_seconds=0.05)
    service = PredictionCacheService(
        predictor=predictor,
        ttl_seconds=900.0,
        clock=clock.now,
        max_refresh_workers=2,
    )

    results: list[float] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            result = service.get_prediction("74")
            results.append(result.top_candidates[0].score)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    assert not errors
    assert predictor.call_count == 1
    assert results == [1.0] * 6


def test_expired_hit_schedules_only_one_refresh_under_concurrency() -> None:
    clock = FakeClock(start=100.0)
    release_refresh = threading.Event()
    predictor = StubPredictor(block_on_calls={2: release_refresh})
    service = PredictionCacheService(
        predictor=predictor,
        ttl_seconds=10.0,
        refresh_retry_cooldown_seconds=1.0,
        clock=clock.now,
        max_refresh_workers=2,
    )

    service.get_prediction("89")
    clock.advance(11.0)

    stale_scores: list[float] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            result = service.get_prediction("89")
            stale_scores.append(result.top_candidates[0].score)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    assert not errors
    assert stale_scores == [1.0] * 8
    assert _wait_until(lambda: predictor.call_count >= 2)
    assert predictor.call_count == 2

    release_refresh.set()
    assert _wait_until(
        lambda: service._cache["89"].value.top_candidates[0].score == 2.0
    )


def test_refresh_failure_keeps_stale_value_available() -> None:
    clock = FakeClock(start=100.0)
    predictor = StubPredictor(fail_on_calls={2})
    service = PredictionCacheService(
        predictor=predictor,
        ttl_seconds=10.0,
        refresh_retry_cooldown_seconds=1.0,
        clock=clock.now,
        max_refresh_workers=1,
    )

    first = service.get_prediction("94")
    clock.advance(11.0)
    stale = service.get_prediction("94")

    assert first.top_candidates[0].score == 1.0
    assert stale.top_candidates[0].score == 1.0
    assert _wait_until(
        lambda: (
            service._cache["94"].refresh_in_flight is False
            and service._cache["94"].last_refresh_error is not None
        )
    )
    assert service.get_prediction("94").top_candidates[0].score == 1.0
