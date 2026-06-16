from __future__ import annotations

import time
from pathlib import Path

from src.polymarket import (
    GatewayPolymarketSnapshotFetcher,
    HybridPairwiseWinModel,
    PolymarketMarketSelection,
    PolymarketSnapshot,
    PolymarketSnapshotStore,
    ThreeWayProbability,
)
from src.world_ranking import MatchContext, MatchOutcome, WorldRankingModelConfig


class FixedTeamPowerModel:
    def team_rating(self, team: str) -> float:
        return {
            "Mexico": 1650.0,
            "South Africa": 1500.0,
        }.get(team, 1500.0)

    def team_rank(self, team: str) -> int:
        return {
            "Mexico": 12,
            "South Africa": 48,
        }.get(team, 100)


class RecordingFallbackPairwiseWinModel:
    def __init__(self) -> None:
        self.group_calls = 0
        self.knockout_calls = 0

    def group_outcomes(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model,
        model_config,
        decisive_band: float,
    ) -> list[MatchOutcome]:
        _ = (home_team, away_team, match_context, team_power_model, model_config, decisive_band)
        self.group_calls += 1
        return [
            MatchOutcome(home_goals=2, away_goals=1, probability=0.4),
            MatchOutcome(home_goals=1, away_goals=1, probability=0.2),
            MatchOutcome(home_goals=1, away_goals=2, probability=0.4),
        ]

    def knockout_home_win_probability(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model,
        model_config,
        draw_band: float,
    ) -> float:
        _ = (home_team, away_team, match_context, team_power_model, model_config, draw_band)
        self.knockout_calls += 1
        return 0.61


class StubSnapshotFetcher:
    def __init__(self, snapshots: list[PolymarketSnapshot] | None = None, error: Exception | None = None) -> None:
        self.snapshots = snapshots or []
        self.error = error
        self.calls = 0

    def fetch_snapshot(self) -> PolymarketSnapshot:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if not self.snapshots:
            raise RuntimeError("No snapshots configured.")
        return self.snapshots[min(self.calls - 1, len(self.snapshots) - 1)]


def _selection(
    *,
    home_win: float,
    draw: float,
    away_win: float,
    age_seconds: float = 0.0,
    max_spread: float = 0.06,
    liquidity: float = 1000.0,
) -> PolymarketMarketSelection:
    now = time.time()
    return PolymarketMarketSelection(
        probabilities=ThreeWayProbability(home_win=home_win, draw=draw, away_win=away_win),
        max_spread=max_spread,
        liquidity=liquidity,
        updated_at_epoch=now - age_seconds,
    )


def _snapshot(selection: PolymarketMarketSelection) -> PolymarketSnapshot:
    return PolymarketSnapshot(
        fetched_at_epoch=time.time(),
        events_seen=1,
        market_selections_by_pair={("Mexico", "South Africa"): selection},
    )


def test_hybrid_pairwise_uses_market_for_group_outcomes() -> None:
    snapshot_store = PolymarketSnapshotStore(
        fetcher=StubSnapshotFetcher([_snapshot(_selection(home_win=0.5, draw=0.25, away_win=0.25))]),
        cache_path=None,
        auto_refresh_on_access=False,
    )
    snapshot_store.refresh_now()

    fallback = RecordingFallbackPairwiseWinModel()
    hybrid = HybridPairwiseWinModel(snapshot_store, fallback=fallback)

    outcomes = hybrid.group_outcomes(
        "Mexico",
        "South Africa",
        MatchContext(match_number=1, stage="group_stage", date="2026-06-11", group="A"),
        team_power_model=FixedTeamPowerModel(),
        model_config=WorldRankingModelConfig(),
        decisive_band=80.0,
    )

    assert [round(outcome.probability, 2) for outcome in outcomes] == [0.5, 0.25, 0.25]
    assert fallback.group_calls == 0


def test_gateway_fetcher_defaults_to_current_world_cup_slug() -> None:
    from src.team_name_normalization import TeamNameNormalizer

    fetcher = GatewayPolymarketSnapshotFetcher(
        normalizer=TeamNameNormalizer.build(canonical_names=["Mexico", "South Africa"])
    )

    assert fetcher.league_slug == "fwc"


def test_gateway_fetcher_ignores_future_start_and_end_dates_for_market_recency() -> None:
    from src.team_name_normalization import TeamNameNormalizer

    fetcher = GatewayPolymarketSnapshotFetcher(
        normalizer=TeamNameNormalizer.build(canonical_names=["Mexico", "South Africa"]),
        time_source=lambda: 1234.0,
    )
    snapshot = fetcher.snapshot_from_events(
        [
            {
                "title": "Mexico vs. South Africa",
                "startDate": "2099-07-01T12:00:00Z",
                "endDate": "2099-07-01T14:00:00Z",
                "markets": [
                    {
                        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
                        "sortOrder": 1,
                        "bestBidQuote": {"value": "0.45"},
                        "bestAskQuote": {"value": "0.47"},
                    },
                    {
                        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
                        "sortOrder": 2,
                        "bestBidQuote": {"value": "0.26"},
                        "bestAskQuote": {"value": "0.28"},
                    },
                    {
                        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
                        "sortOrder": 3,
                        "bestBidQuote": {"value": "0.28"},
                        "bestAskQuote": {"value": "0.30"},
                    },
                ],
            }
        ]
    )

    selection = snapshot.market_selections_by_pair[("Mexico", "South Africa")]
    assert selection.updated_at_epoch is None
    assert snapshot.fetched_at_epoch == 1234.0


def test_gateway_fetcher_prefers_true_updated_timestamp_over_schedule_dates() -> None:
    from src.team_name_normalization import TeamNameNormalizer

    fetcher = GatewayPolymarketSnapshotFetcher(
        normalizer=TeamNameNormalizer.build(canonical_names=["Mexico", "South Africa"]),
        time_source=lambda: 1234.0,
    )
    snapshot = fetcher.snapshot_from_events(
        [
            {
                "title": "Mexico vs. South Africa",
                "updatedAt": "2026-06-16T12:34:56Z",
                "startDate": "2099-07-01T12:00:00Z",
                "endDate": "2099-07-01T14:00:00Z",
                "markets": [
                    {
                        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
                        "sortOrder": 1,
                        "bestBidQuote": {"value": "0.45"},
                        "bestAskQuote": {"value": "0.47"},
                    },
                    {
                        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
                        "sortOrder": 2,
                        "bestBidQuote": {"value": "0.26"},
                        "bestAskQuote": {"value": "0.28"},
                    },
                    {
                        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
                        "sortOrder": 3,
                        "bestBidQuote": {"value": "0.28"},
                        "bestAskQuote": {"value": "0.30"},
                    },
                ],
            }
        ]
    )

    selection = snapshot.market_selections_by_pair[("Mexico", "South Africa")]
    assert selection.updated_at_epoch == 1781613296.0


def test_hybrid_pairwise_falls_back_when_snapshot_missing() -> None:
    snapshot_store = PolymarketSnapshotStore(
        fetcher=StubSnapshotFetcher([]),
        cache_path=None,
        auto_refresh_on_access=False,
    )
    fallback = RecordingFallbackPairwiseWinModel()
    hybrid = HybridPairwiseWinModel(snapshot_store, fallback=fallback)

    outcomes = hybrid.group_outcomes(
        "Mexico",
        "South Africa",
        MatchContext(match_number=1, stage="group_stage", date="2026-06-11", group="A"),
        team_power_model=FixedTeamPowerModel(),
        model_config=WorldRankingModelConfig(),
        decisive_band=80.0,
    )

    assert [round(outcome.probability, 2) for outcome in outcomes] == [0.4, 0.2, 0.4]
    assert fallback.group_calls == 1


def test_hybrid_pairwise_falls_back_when_market_is_stale() -> None:
    snapshot_store = PolymarketSnapshotStore(
        fetcher=StubSnapshotFetcher([_snapshot(_selection(home_win=0.5, draw=0.25, away_win=0.25, age_seconds=7200))]),
        cache_path=None,
        auto_refresh_on_access=False,
    )
    snapshot_store.refresh_now()

    fallback = RecordingFallbackPairwiseWinModel()
    hybrid = HybridPairwiseWinModel(
        snapshot_store,
        fallback=fallback,
        max_market_age_seconds=900.0,
    )

    probability = hybrid.knockout_home_win_probability(
        "Mexico",
        "South Africa",
        MatchContext(match_number=82, stage="round_of_16", date="2026-07-04", group=None),
        team_power_model=FixedTeamPowerModel(),
        model_config=WorldRankingModelConfig(),
        draw_band=20.0,
    )

    assert probability == 0.61
    assert fallback.knockout_calls == 1


def test_hybrid_pairwise_uses_snapshot_fetch_time_when_market_update_time_missing() -> None:
    selection = PolymarketMarketSelection(
        probabilities=ThreeWayProbability(home_win=0.5, draw=0.25, away_win=0.25),
        max_spread=0.05,
        liquidity=1000.0,
        updated_at_epoch=None,
    )
    snapshot = PolymarketSnapshot(
        fetched_at_epoch=time.time(),
        events_seen=1,
        market_selections_by_pair={("Mexico", "South Africa"): selection},
    )
    snapshot_store = PolymarketSnapshotStore(
        fetcher=StubSnapshotFetcher([snapshot]),
        cache_path=None,
        auto_refresh_on_access=False,
    )
    snapshot_store.refresh_now()

    fallback = RecordingFallbackPairwiseWinModel()
    hybrid = HybridPairwiseWinModel(
        snapshot_store,
        fallback=fallback,
        max_market_age_seconds=900.0,
    )

    probability = hybrid.knockout_home_win_probability(
        "Mexico",
        "South Africa",
        MatchContext(match_number=82, stage="round_of_16", date="2026-07-04", group=None),
        team_power_model=FixedTeamPowerModel(),
        model_config=WorldRankingModelConfig(),
        draw_band=20.0,
    )

    assert probability != 0.61
    assert fallback.knockout_calls == 0


def test_snapshot_store_preserves_last_known_good_when_refresh_fails(tmp_path: Path) -> None:
    good_snapshot = _snapshot(_selection(home_win=0.45, draw=0.30, away_win=0.25))
    fetcher = StubSnapshotFetcher([good_snapshot])
    store = PolymarketSnapshotStore(
        fetcher=fetcher,
        cache_path=tmp_path / "polymarket_snapshot.json",
        auto_refresh_on_access=False,
    )

    first = store.refresh_now()
    assert first.market_selections_by_pair[("Mexico", "South Africa")].probabilities.home_win == 0.45

    store.fetcher = StubSnapshotFetcher(error=RuntimeError("provider unstable"))

    try:
        store.refresh_now()
    except RuntimeError:
        pass

    preserved = store.get_snapshot()
    assert preserved is not None
    assert preserved.market_selections_by_pair[("Mexico", "South Africa")].probabilities.home_win == 0.45
    status = store.status()
    assert status.has_snapshot is True
    assert status.last_refresh_error == "provider unstable"


def test_snapshot_store_reports_refresh_error_without_existing_snapshot() -> None:
    store = PolymarketSnapshotStore(
        fetcher=StubSnapshotFetcher(error=RuntimeError("provider unavailable")),
        cache_path=None,
        auto_refresh_on_access=False,
    )

    try:
        store.refresh_now()
    except RuntimeError:
        pass

    status = store.status()
    assert status.has_snapshot is False
    assert status.last_refresh_attempt_at is not None
    assert status.last_refresh_error == "provider unavailable"


def test_snapshot_store_rejects_empty_refresh_without_overwriting_last_good(tmp_path: Path) -> None:
    good_snapshot = _snapshot(_selection(home_win=0.45, draw=0.30, away_win=0.25))
    store = PolymarketSnapshotStore(
        fetcher=StubSnapshotFetcher([good_snapshot]),
        cache_path=tmp_path / "polymarket_snapshot.json",
        auto_refresh_on_access=False,
    )
    store.refresh_now()
    store.fetcher = StubSnapshotFetcher(
        [
            PolymarketSnapshot(
                fetched_at_epoch=time.time(),
                events_seen=0,
                market_selections_by_pair={},
            )
        ]
    )

    try:
        store.refresh_now()
    except RuntimeError as exc:
        assert str(exc) == "Polymarket refresh returned no events."

    preserved = store.get_snapshot()
    assert preserved is not None
    assert preserved.events_seen == 1
    assert store.status().last_refresh_error == "Polymarket refresh returned no events."


def test_snapshot_store_ignores_empty_persisted_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "polymarket_snapshot.json"
    cache_path.write_text(
        '{"fetched_at_epoch": 123.0, "events_seen": 0, "market_selections": []}',
        encoding="utf-8",
    )
    store = PolymarketSnapshotStore(
        fetcher=StubSnapshotFetcher([]),
        cache_path=cache_path,
        auto_refresh_on_access=False,
    )

    assert store.get_snapshot() is None
    assert store.status().has_snapshot is False


def test_snapshot_store_loads_last_known_good_from_disk(tmp_path: Path) -> None:
    good_snapshot = _snapshot(_selection(home_win=0.48, draw=0.22, away_win=0.30))
    fetcher = StubSnapshotFetcher([good_snapshot])
    cache_path = tmp_path / "polymarket_snapshot.json"

    first_store = PolymarketSnapshotStore(
        fetcher=fetcher,
        cache_path=cache_path,
        auto_refresh_on_access=False,
    )
    first_store.refresh_now()

    restored_store = PolymarketSnapshotStore(
        fetcher=StubSnapshotFetcher([]),
        cache_path=cache_path,
        auto_refresh_on_access=False,
    )
    restored_snapshot = restored_store.get_snapshot()

    assert restored_snapshot is not None
    assert restored_snapshot.market_selections_by_pair[("Mexico", "South Africa")].probabilities.away_win == 0.30


def test_market_mode_usage_summary_marks_visible_fallback() -> None:
    snapshot_store = PolymarketSnapshotStore(
        fetcher=StubSnapshotFetcher([]),
        cache_path=None,
        auto_refresh_on_access=False,
    )
    fallback = RecordingFallbackPairwiseWinModel()
    model = HybridPairwiseWinModel(snapshot_store, fallback=fallback, mode_label="market")

    model.group_outcomes(
        "Mexico",
        "South Africa",
        MatchContext(match_number=1, stage="group_stage", date="2026-06-11", group="A"),
        team_power_model=FixedTeamPowerModel(),
        model_config=WorldRankingModelConfig(),
        decisive_band=80.0,
    )

    summary = model.usage_summary()
    assert summary["strength_mode"] == "market"
    assert summary["fallback_visible"] is True
    assert summary["fallback_reasons"] == {"no_snapshot": 1}
