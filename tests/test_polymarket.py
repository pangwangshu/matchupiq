from __future__ import annotations

import time
from pathlib import Path

from src.polymarket import (
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
