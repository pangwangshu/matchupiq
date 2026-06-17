from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

try:
    from src.team_name_normalization import TeamNameNormalizer
    from src.world_ranking import (
        MatchContext,
        MatchOutcome,
        PairwiseWinModel,
        RatingPairwiseWinModel,
        TeamPowerModel,
        WorldRankingModelConfig,
    )
except ModuleNotFoundError:
    from team_name_normalization import TeamNameNormalizer
    from world_ranking import (
        MatchContext,
        MatchOutcome,
        PairwiseWinModel,
        RatingPairwiseWinModel,
        TeamPowerModel,
        WorldRankingModelConfig,
    )

logger = logging.getLogger(__name__)

DEFAULT_POLYMARKET_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "polymarket_last_snapshot.json"
)


@dataclass(frozen=True)
class ThreeWayProbability:
    """Normalized home/draw/away probability triple from a market snapshot."""

    home_win: float
    draw: float
    away_win: float


@dataclass(frozen=True)
class PolymarketMarketSelection:
    """Selected market data for a single canonical team pairing."""

    probabilities: ThreeWayProbability
    max_spread: float | None
    liquidity: float | None
    updated_at_epoch: float | None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the selection for cache persistence."""
        return {
            "probabilities": {
                "home_win": self.probabilities.home_win,
                "draw": self.probabilities.draw,
                "away_win": self.probabilities.away_win,
            },
            "max_spread": self.max_spread,
            "liquidity": self.liquidity,
            "updated_at_epoch": self.updated_at_epoch,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PolymarketMarketSelection":
        """Restore a persisted market selection from a dictionary payload."""
        probabilities = payload.get("probabilities", {})
        return cls(
            probabilities=ThreeWayProbability(
                home_win=float(probabilities.get("home_win", 0.0)),
                draw=float(probabilities.get("draw", 0.0)),
                away_win=float(probabilities.get("away_win", 0.0)),
            ),
            max_spread=(
                None if payload.get("max_spread") is None else float(payload["max_spread"])
            ),
            liquidity=(
                None if payload.get("liquidity") is None else float(payload["liquidity"])
            ),
            updated_at_epoch=(
                None
                if payload.get("updated_at_epoch") is None
                else float(payload["updated_at_epoch"])
            ),
        )


@dataclass(frozen=True)
class PolymarketSnapshot:
    """Cached view of all usable Polymarket selections for the tournament."""

    fetched_at_epoch: float
    events_seen: int
    market_selections_by_pair: dict[tuple[str, str], PolymarketMarketSelection]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the snapshot to a JSON-friendly structure."""
        return {
            "fetched_at_epoch": self.fetched_at_epoch,
            "events_seen": self.events_seen,
            "market_selections": [
                {
                    "home_team": home,
                    "away_team": away,
                    **selection.to_dict(),
                }
                for (home, away), selection in sorted(self.market_selections_by_pair.items())
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PolymarketSnapshot":
        """Restore a serialized snapshot from disk or another cache layer."""
        raw_markets = payload.get("market_selections", [])
        market_selections_by_pair: dict[tuple[str, str], PolymarketMarketSelection] = {}
        if isinstance(raw_markets, list):
            for item in raw_markets:
                if not isinstance(item, dict):
                    continue
                home_team = str(item.get("home_team", "")).strip()
                away_team = str(item.get("away_team", "")).strip()
                if not home_team or not away_team:
                    continue
                market_selections_by_pair[(home_team, away_team)] = PolymarketMarketSelection.from_dict(item)
        return cls(
            fetched_at_epoch=float(payload.get("fetched_at_epoch", 0.0)),
            events_seen=int(payload.get("events_seen", 0)),
            market_selections_by_pair=market_selections_by_pair,
        )


class PolymarketSnapshotFetcher(Protocol):
    """Fetches a fresh Polymarket snapshot from an external provider."""

    def fetch_snapshot(self) -> PolymarketSnapshot:
        """Return the latest snapshot of usable market selections."""
        ...


def _parse_iso_datetime_to_epoch(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return __import__("datetime").datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _extract_numeric(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class GatewayPolymarketSnapshotFetcher:
    """Loads and normalizes market data from the Polymarket gateway API."""

    def __init__(
        self,
        normalizer: TeamNameNormalizer,
        *,
        league_slug: str = "fwc",
        page_size: int = 20,
        request_timeout_seconds: float = 3.0,
        max_retries: int = 2,
        backoff_seconds: float = 0.35,
        user_agent: str = "Mozilla/5.0 (compatible; which-matchup/polymarket-integration)",
        url_opener: Any = urllib.request.urlopen,
        time_source: callable | None = None,
    ) -> None:
        self.normalizer = normalizer
        self.league_slug = league_slug
        self.page_size = page_size
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.user_agent = user_agent
        self.url_opener = url_opener
        self.time_source = time_source or time.time

    def _fetch_json(self, url: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                },
            )
            try:
                with self.url_opener(request, timeout=self.request_timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:  # pragma: no cover - defensive network path
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self.backoff_seconds * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _parse_event_pair(self, title: str) -> tuple[str, str] | None:
        if " vs. " not in title:
            return None
        left, right = title.split(" vs. ", 1)
        left_name = self.normalizer.resolve(left.strip())
        right_name = self.normalizer.resolve(right.strip())
        if left_name is None or right_name is None:
            return None
        return left_name, right_name

    def _to_midpoint_probability(self, market: dict[str, Any]) -> float | None:
        bid = _extract_numeric(market.get("bestBidQuote", {}).get("value"))
        ask = _extract_numeric(market.get("bestAskQuote", {}).get("value"))
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _to_spread(self, market: dict[str, Any]) -> float | None:
        bid = _extract_numeric(market.get("bestBidQuote", {}).get("value"))
        ask = _extract_numeric(market.get("bestAskQuote", {}).get("value"))
        if bid is None or ask is None:
            return None
        return max(0.0, ask - bid)

    def _extract_liquidity(self, event: dict[str, Any], markets: list[dict[str, Any]]) -> float | None:
        candidate_values: list[float] = []
        for source in [event, *markets]:
            if not isinstance(source, dict):
                continue
            for key in (
                "liquidity",
                "liquidityClob",
                "volume",
                "volumeClob",
                "sportsLiquidity",
                "sportsLiquidityClob",
            ):
                value = _extract_numeric(source.get(key))
                if value is not None:
                    candidate_values.append(value)
        if not candidate_values:
            return None
        return max(candidate_values)

    def _extract_updated_at_epoch(self, event: dict[str, Any], markets: list[dict[str, Any]]) -> float | None:
        candidates: list[float] = []
        for source in [event, *markets]:
            if not isinstance(source, dict):
                continue
            for key in ("updatedAt", "updated_at", "lastUpdated"):
                timestamp = _parse_iso_datetime_to_epoch(source.get(key))
                if timestamp is not None:
                    candidates.append(timestamp)
        if not candidates:
            return None
        return max(candidates)

    def _event_market_selection(self, event: dict[str, Any]) -> PolymarketMarketSelection | None:
        markets = event.get("markets", [])
        if not isinstance(markets, list):
            return None

        by_order: dict[int, dict[str, Any]] = {}
        for market in markets:
            if not isinstance(market, dict):
                continue
            if market.get("sportsMarketTypeV2") != "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME":
                continue
            order = market.get("sortOrder")
            if order in (1, 2, 3) and int(order) not in by_order:
                by_order[int(order)] = market

        if set(by_order.keys()) != {1, 2, 3}:
            return None

        p_home = self._to_midpoint_probability(by_order[1])
        p_draw = self._to_midpoint_probability(by_order[2])
        p_away = self._to_midpoint_probability(by_order[3])
        if p_home is None or p_draw is None or p_away is None:
            return None

        total = p_home + p_draw + p_away
        if total <= 0.0:
            return None

        spreads = [
            spread
            for spread in (
                self._to_spread(by_order[1]),
                self._to_spread(by_order[2]),
                self._to_spread(by_order[3]),
            )
            if spread is not None
        ]

        return PolymarketMarketSelection(
            probabilities=ThreeWayProbability(
                home_win=p_home / total,
                draw=p_draw / total,
                away_win=p_away / total,
            ),
            max_spread=max(spreads) if spreads else None,
            liquidity=self._extract_liquidity(event, [by_order[1], by_order[2], by_order[3]]),
            updated_at_epoch=self._extract_updated_at_epoch(event, [by_order[1], by_order[2], by_order[3]]),
        )

    def fetch_events(self) -> list[dict[str, Any]]:
        """Fetch raw league events from the Polymarket gateway."""
        events_seen: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._fetch_json(
                "https://gateway.polymarket.us/v2/leagues/"
                f"{self.league_slug}/events?type=sport&section=general&limit={self.page_size}&offset={offset}"
            )
            events = payload.get("events", [])
            if not isinstance(events, list) or not events:
                break
            events_seen.extend(event for event in events if isinstance(event, dict))
            offset += self.page_size
        return events_seen

    def snapshot_from_events(self, events: list[dict[str, Any]]) -> PolymarketSnapshot:
        """Build a normalized snapshot from previously fetched raw events."""
        market_selections_by_pair: dict[tuple[str, str], PolymarketMarketSelection] = {}
        for event in events:
            if not isinstance(event, dict):
                continue
            pair = self._parse_event_pair(str(event.get("title", "")))
            if pair is None:
                continue
            selection = self._event_market_selection(event)
            if selection is None:
                continue
            market_selections_by_pair[pair] = selection

        return PolymarketSnapshot(
            fetched_at_epoch=self.time_source(),
            events_seen=len([event for event in events if isinstance(event, dict)]),
            market_selections_by_pair=market_selections_by_pair,
        )

    def fetch_snapshot(self) -> PolymarketSnapshot:
        """Fetch all usable market selections for the configured league."""
        return self.snapshot_from_events(self.fetch_events())


@dataclass
class SnapshotCacheEntry:
    """In-memory cache state for the current Polymarket snapshot."""

    snapshot: PolymarketSnapshot
    fresh_until_monotonic: float
    serve_until_monotonic: float
    refresh_in_flight: bool = False
    last_refresh_attempt_at: float | None = None
    last_refresh_error: str | None = None


@dataclass(frozen=True)
class PolymarketSnapshotStatus:
    """User-facing cache and refresh state for the current market snapshot."""

    has_snapshot: bool
    is_fresh: bool
    fetched_at_epoch: float | None
    events_seen: int
    market_count: int
    fresh_ttl_seconds: float
    serve_stale_ttl_seconds: float
    refresh_in_flight: bool
    last_refresh_attempt_at: float | None
    last_refresh_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_snapshot": self.has_snapshot,
            "is_fresh": self.is_fresh,
            "fetched_at_epoch": self.fetched_at_epoch,
            "events_seen": self.events_seen,
            "market_count": self.market_count,
            "fresh_ttl_seconds": self.fresh_ttl_seconds,
            "serve_stale_ttl_seconds": self.serve_stale_ttl_seconds,
            "refresh_in_flight": self.refresh_in_flight,
            "last_refresh_attempt_at": self.last_refresh_attempt_at,
            "last_refresh_error": self.last_refresh_error,
        }


class PolymarketSnapshotStore:
    """Caches market snapshots with stale-while-refresh semantics."""

    def __init__(
        self,
        fetcher: PolymarketSnapshotFetcher,
        *,
        cache_path: Path | None = DEFAULT_POLYMARKET_CACHE_PATH,
        fresh_ttl_seconds: float = 300.0,
        serve_stale_ttl_seconds: float = 1800.0,
        refresh_retry_cooldown_seconds: float = 30.0,
        max_refresh_workers: int = 1,
        auto_refresh_on_access: bool = True,
        clock: callable | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.cache_path = cache_path
        self.fresh_ttl_seconds = fresh_ttl_seconds
        self.serve_stale_ttl_seconds = serve_stale_ttl_seconds
        self.refresh_retry_cooldown_seconds = refresh_retry_cooldown_seconds
        self.auto_refresh_on_access = auto_refresh_on_access
        self.clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._max_refresh_workers = max_refresh_workers
        self._refresh_executor: ThreadPoolExecutor | None = None
        self._last_refresh_attempt_at: float | None = None
        self._last_refresh_error: str | None = None
        self._cache_mtime: float | None = None
        self._entry: SnapshotCacheEntry | None = self._load_cache_entry()

    def _snapshot_has_market_data(self, snapshot: PolymarketSnapshot) -> bool:
        return snapshot.events_seen > 0 and bool(snapshot.market_selections_by_pair)

    def _validate_refresh_snapshot(self, snapshot: PolymarketSnapshot) -> None:
        if snapshot.events_seen <= 0:
            raise RuntimeError("Polymarket refresh returned no events.")
        if not snapshot.market_selections_by_pair:
            raise RuntimeError("Polymarket refresh returned no usable market selections.")

    def _load_cache_entry(self) -> SnapshotCacheEntry | None:
        if self.cache_path is None or not self.cache_path.exists():
            return None
        try:
            self._cache_mtime = self.cache_path.stat().st_mtime
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            snapshot = PolymarketSnapshot.from_dict(payload)
            if not self._snapshot_has_market_data(snapshot):
                return None
        except Exception:
            logger.exception("polymarket_cache_load_failed path=%s", self.cache_path)
            return None
        now = self.clock()
        return SnapshotCacheEntry(
            snapshot=snapshot,
            fresh_until_monotonic=now,
            serve_until_monotonic=now + self.serve_stale_ttl_seconds,
        )

    def _maybe_reload_cache_entry_from_disk_locked(self) -> None:
        if self.cache_path is None or not self.cache_path.exists():
            return
        if self._entry is not None and self._entry.refresh_in_flight:
            return
        try:
            cache_mtime = self.cache_path.stat().st_mtime
        except OSError:
            return
        if self._cache_mtime is not None and cache_mtime <= self._cache_mtime:
            return

        reloaded_entry = self._load_cache_entry()
        if reloaded_entry is None:
            return
        self._entry = reloaded_entry

    def _store_snapshot(self, snapshot: PolymarketSnapshot) -> None:
        if self.cache_path is None:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(snapshot.to_dict(), indent=2),
                encoding="utf-8",
            )
            self._cache_mtime = self.cache_path.stat().st_mtime
        except Exception:
            logger.exception("polymarket_cache_write_failed path=%s", self.cache_path)

    def get_snapshot(self) -> PolymarketSnapshot | None:
        """Return the freshest available snapshot, optionally triggering refresh."""
        now = self.clock()
        with self._lock:
            self._maybe_reload_cache_entry_from_disk_locked()
            entry = self._entry
            if entry is not None:
                if entry.fresh_until_monotonic > now:
                    return entry.snapshot
                if entry.serve_until_monotonic > now:
                    if self.auto_refresh_on_access:
                        self._maybe_schedule_refresh_locked(now)
                    return entry.snapshot
                self._entry = None

            if self.auto_refresh_on_access:
                self._maybe_schedule_refresh_locked(now)
            return None

    def refresh_now(self) -> PolymarketSnapshot:
        """Fetch and store a fresh snapshot immediately in the current thread."""
        now = self.clock()
        with self._lock:
            self._last_refresh_attempt_at = now
            self._last_refresh_error = None
            if self._entry is not None:
                self._entry.last_refresh_attempt_at = now
                self._entry.last_refresh_error = None

        try:
            snapshot = self.fetcher.fetch_snapshot()
            self._validate_refresh_snapshot(snapshot)
        except Exception as exc:
            with self._lock:
                self._last_refresh_error = str(exc)
                if self._entry is not None:
                    self._entry.last_refresh_error = str(exc)
                    self._entry.refresh_in_flight = False
            raise

        now = self.clock()
        with self._lock:
            self._last_refresh_attempt_at = now
            self._last_refresh_error = None
            self._entry = SnapshotCacheEntry(
                snapshot=snapshot,
                fresh_until_monotonic=now + self.fresh_ttl_seconds,
                serve_until_monotonic=now + self.serve_stale_ttl_seconds,
                last_refresh_attempt_at=now,
            )
            self._store_snapshot(snapshot)
        return snapshot

    @property
    def last_refresh_error(self) -> str | None:
        """Return the most recent background refresh error, if any."""
        with self._lock:
            if self._entry is not None and self._entry.last_refresh_error is not None:
                return self._entry.last_refresh_error
            return self._last_refresh_error

    def status(self) -> PolymarketSnapshotStatus:
        """Return a serializable view of cache freshness and refresh state."""
        now = self.clock()
        with self._lock:
            self._maybe_reload_cache_entry_from_disk_locked()
            entry = self._entry
            if entry is None:
                return PolymarketSnapshotStatus(
                    has_snapshot=False,
                    is_fresh=False,
                    fetched_at_epoch=None,
                    events_seen=0,
                    market_count=0,
                    fresh_ttl_seconds=self.fresh_ttl_seconds,
                    serve_stale_ttl_seconds=self.serve_stale_ttl_seconds,
                    refresh_in_flight=False,
                    last_refresh_attempt_at=self._last_refresh_attempt_at,
                    last_refresh_error=self._last_refresh_error,
                )
            has_market_data = self._snapshot_has_market_data(entry.snapshot)
            return PolymarketSnapshotStatus(
                has_snapshot=has_market_data,
                is_fresh=has_market_data and entry.fresh_until_monotonic > now,
                fetched_at_epoch=entry.snapshot.fetched_at_epoch if has_market_data else None,
                events_seen=entry.snapshot.events_seen if has_market_data else 0,
                market_count=len(entry.snapshot.market_selections_by_pair) if has_market_data else 0,
                fresh_ttl_seconds=self.fresh_ttl_seconds,
                serve_stale_ttl_seconds=self.serve_stale_ttl_seconds,
                refresh_in_flight=entry.refresh_in_flight,
                last_refresh_attempt_at=entry.last_refresh_attempt_at or self._last_refresh_attempt_at,
                last_refresh_error=entry.last_refresh_error or self._last_refresh_error,
            )

    def _maybe_schedule_refresh_locked(self, now: float) -> None:
        entry = self._entry
        if entry is not None:
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
        else:
            self._entry = SnapshotCacheEntry(
                snapshot=PolymarketSnapshot(fetched_at_epoch=0.0, events_seen=0, market_selections_by_pair={}),
                fresh_until_monotonic=now,
                serve_until_monotonic=now,
                refresh_in_flight=True,
                last_refresh_attempt_at=now,
                last_refresh_error=None,
            )

        if self._refresh_executor is None:
            self._refresh_executor = ThreadPoolExecutor(
                max_workers=self._max_refresh_workers,
                thread_name_prefix="polymarket-refresh",
            )
        future = self._refresh_executor.submit(self.fetcher.fetch_snapshot)
        future.add_done_callback(self._on_refresh_done)

    def _on_refresh_done(self, future: Future[PolymarketSnapshot]) -> None:
        now = self.clock()
        with self._lock:
            entry = self._entry
            if entry is None:
                return

            try:
                snapshot = future.result()
                self._validate_refresh_snapshot(snapshot)
            except Exception as exc:
                entry.refresh_in_flight = False
                entry.last_refresh_error = str(exc)
                self._last_refresh_error = str(exc)
                if not self._snapshot_has_market_data(entry.snapshot):
                    self._entry = None
                logger.exception("polymarket_refresh_failed")
                return

            self._entry = SnapshotCacheEntry(
                snapshot=snapshot,
                fresh_until_monotonic=now + self.fresh_ttl_seconds,
                serve_until_monotonic=now + self.serve_stale_ttl_seconds,
                refresh_in_flight=False,
                last_refresh_attempt_at=entry.last_refresh_attempt_at,
                last_refresh_error=None,
            )
            self._store_snapshot(snapshot)


class HybridPairwiseWinModel(PairwiseWinModel):
    """Market-first pairwise model with deterministic rating fallback."""

    def __init__(
        self,
        snapshot_store: PolymarketSnapshotStore,
        *,
        fallback: PairwiseWinModel | None = None,
        max_market_age_seconds: float = 900.0,
        max_market_spread: float = 0.18,
        min_market_liquidity: float = 0.0,
        mode_label: str = "hybrid",
    ) -> None:
        self.snapshot_store = snapshot_store
        self.fallback = fallback or RatingPairwiseWinModel()
        self.max_market_age_seconds = max_market_age_seconds
        self.max_market_spread = max_market_spread
        self.min_market_liquidity = min_market_liquidity
        self.mode_label = mode_label
        self.market_hits = 0
        self.fallback_hits = 0
        self.fallback_reasons: dict[str, int] = {}

    def reset_usage(self) -> None:
        """Clear per-prediction usage counters."""
        self.market_hits = 0
        self.fallback_hits = 0
        self.fallback_reasons = {}

    def usage_summary(self) -> dict[str, Any]:
        """Return user-facing source usage metadata for the latest prediction."""
        return {
            "strength_mode": self.mode_label,
            "market_hits": self.market_hits,
            "fallback_hits": self.fallback_hits,
            "fallback_reasons": dict(sorted(self.fallback_reasons.items())),
            "fallback_visible": self.mode_label == "market" and self.fallback_hits > 0,
        }

    def _record_fallback(
        self,
        *,
        reason: str,
        mode: str,
        match_context: MatchContext,
        home_team: str,
        away_team: str,
    ) -> None:
        self.fallback_hits += 1
        self.fallback_reasons[reason] = self.fallback_reasons.get(reason, 0) + 1
        logger.info(
            "polymarket_%s_fallback reason=%s match_number=%s home=%s away=%s",
            mode,
            reason,
            match_context.match_number,
            home_team,
            away_team,
        )

    def _lookup_market(
        self,
        home_team: str,
        away_team: str,
    ) -> tuple[ThreeWayProbability | None, str]:
        snapshot = self.snapshot_store.get_snapshot()
        if snapshot is None:
            return None, "no_snapshot"

        selection = snapshot.market_selections_by_pair.get((home_team, away_team))
        reversed_market = False
        if selection is None:
            selection = snapshot.market_selections_by_pair.get((away_team, home_team))
            reversed_market = selection is not None
        if selection is None:
            return None, "missing_market"

        now_epoch = time.time()
        updated_at_epoch = selection.updated_at_epoch or snapshot.fetched_at_epoch
        age_seconds = max(0.0, now_epoch - updated_at_epoch)
        if age_seconds > self.max_market_age_seconds:
            return None, "stale_market"

        if selection.max_spread is not None and selection.max_spread > self.max_market_spread:
            return None, "wide_spread"

        if selection.liquidity is not None and selection.liquidity < self.min_market_liquidity:
            return None, "low_liquidity"

        if not reversed_market:
            return selection.probabilities, "market"
        return (
            ThreeWayProbability(
                home_win=selection.probabilities.away_win,
                draw=selection.probabilities.draw,
                away_win=selection.probabilities.home_win,
            ),
            "market",
        )

    def _fallback_group_outcomes(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model: TeamPowerModel,
        model_config: WorldRankingModelConfig,
        decisive_band: float,
        reason: str,
    ) -> list[MatchOutcome]:
        self._record_fallback(
            reason=reason,
            mode="group",
            match_context=match_context,
            home_team=home_team,
            away_team=away_team,
        )
        return self.fallback.group_outcomes(
            home_team,
            away_team,
            match_context,
            team_power_model=team_power_model,
            model_config=model_config,
            decisive_band=decisive_band,
        )

    def _fallback_knockout_probability(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model: TeamPowerModel,
        model_config: WorldRankingModelConfig,
        draw_band: float,
        reason: str,
    ) -> float:
        self._record_fallback(
            reason=reason,
            mode="knockout",
            match_context=match_context,
            home_team=home_team,
            away_team=away_team,
        )
        return self.fallback.knockout_home_win_probability(
            home_team,
            away_team,
            match_context,
            team_power_model=team_power_model,
            model_config=model_config,
            draw_band=draw_band,
        )

    def group_outcomes(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model: TeamPowerModel,
        model_config: WorldRankingModelConfig,
        decisive_band: float,
    ) -> list[MatchOutcome]:
        """Return market-backed group outcomes or delegate to the fallback model."""
        market_probs, reason = self._lookup_market(home_team, away_team)
        if market_probs is None:
            return self._fallback_group_outcomes(
                home_team,
                away_team,
                match_context,
                team_power_model=team_power_model,
                model_config=model_config,
                decisive_band=decisive_band,
                reason=reason,
            )

        self.market_hits += 1
        diff = abs(team_power_model.team_rating(home_team) - team_power_model.team_rating(away_team))
        margin = 2 if diff >= decisive_band else 1
        return [
            MatchOutcome(home_goals=1 + margin, away_goals=1, probability=market_probs.home_win),
            MatchOutcome(home_goals=1, away_goals=1, probability=market_probs.draw),
            MatchOutcome(home_goals=1, away_goals=1 + margin, probability=market_probs.away_win),
        ]

    def knockout_home_win_probability(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model: TeamPowerModel,
        model_config: WorldRankingModelConfig,
        draw_band: float,
    ) -> float:
        """Return market-backed knockout odds or delegate to the fallback model."""
        market_probs, reason = self._lookup_market(home_team, away_team)
        if market_probs is None:
            return self._fallback_knockout_probability(
                home_team,
                away_team,
                match_context,
                team_power_model=team_power_model,
                model_config=model_config,
                draw_band=draw_band,
                reason=reason,
            )

        self.market_hits += 1
        decisive_total = market_probs.home_win + market_probs.away_win
        if decisive_total <= 0.0:
            return 0.5
        return market_probs.home_win / decisive_total
