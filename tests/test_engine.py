from __future__ import annotations

import pytest

from src.engine import (
    MatchDataProvider,
    MatchupPredictor,
    PredictorRuntimeConfig,
    WorldRankingSimulatorFactory,
)
from src.polymarket import HybridPairwiseWinModel, PolymarketSnapshotStore
from src.tournament import TournamentStructureResolver
from src.world_ranking import RatingPairwiseWinModel, WorldRankingModelConfig, WorldRankingTournamentSimulator


class FastTestWorldRankingSimulatorFactory(WorldRankingSimulatorFactory):
    """Use smaller beams so engine tests cover wiring without rerunning production-size searches."""

    def __init__(
        self,
        *,
        config: PredictorRuntimeConfig | None = None,
        polymarket_snapshot_store: PolymarketSnapshotStore | None = None,
    ) -> None:
        super().__init__(
            config=config or PredictorRuntimeConfig(strength_mode="fifa"),
            polymarket_snapshot_store=polymarket_snapshot_store,
        )
        self.model_config = WorldRankingModelConfig(
            prediction_group_beam_width=12,
            prediction_group_max_scenarios=6,
            prediction_world_beam_width=24,
            prediction_min_branch_probability=1e-4,
        )

    def create(
        self,
        world_cup_data,
        team_power_model,
        pairwise_win_model,
        match_results=None,
    ):
        return WorldRankingTournamentSimulator(
            world_cup_data=world_cup_data,
            team_power_model=team_power_model,
            pairwise_win_model=pairwise_win_model,
            match_results=match_results,
            model_config=self.model_config,
            model_config_path=None,
        )


@pytest.fixture
def fast_predictor() -> MatchupPredictor:
    return MatchupPredictor(simulator_factory=FastTestWorldRankingSimulatorFactory())


def _resolver() -> TournamentStructureResolver:
    predictor = MatchupPredictor()
    world = predictor._load_world_cup_data()
    return TournamentStructureResolver(
        groups=world["groups"],
        schedule=world["schedule"],
    )


def test_parse_group_slot_covers_winners_runners_up_and_third_place() -> None:
    resolver = _resolver()

    winners = resolver.parse_group_slot("Group E winners")
    assert winners == ({"E"}, "winners")

    runners_up = resolver.parse_group_slot("Group L runners-up")
    assert runners_up == ({"L"}, "runners-up")

    third_place_multi = resolver.parse_group_slot("Group A/B/C third place")
    assert third_place_multi == ({"A", "B", "C"}, "third place")


def test_predict_non_numeric_match_id_falls_back_to_baseline(fast_predictor: MatchupPredictor) -> None:
    predictor = fast_predictor
    result = predictor.predict("400021525")

    assert result.status == "predicted"
    assert len(result.top_candidates) == 10
    assert all(
        c.reason == "Baseline UI test: matchup can be any two different participant teams."
        for c in result.top_candidates
    )


def test_predict_group_stage_match_has_multiple_candidates(fast_predictor: MatchupPredictor) -> None:
    predictor = fast_predictor
    result = predictor.predict("1")

    assert result.status == "predicted"
    assert 1 <= len(result.top_candidates) <= 10
    assert "scenario-search simulation" in result.top_candidates[0].reason

    # Group-stage match is fixed by schedule, so top pair should be the scheduled one.
    top = result.top_candidates[0]
    assert (top.home_team, top.away_team) == ("Mexico", "South Africa")
    assert top.score > 0


@pytest.mark.parametrize("match_id", ["74", "80", "82", "89", "94", "103", "104"])
def test_predict_knockout_candidates_respect_rule_space(match_id: str, fast_predictor: MatchupPredictor) -> None:
    predictor = fast_predictor
    result = predictor.predict(match_id)

    assert result.status == "predicted"
    assert 1 <= len(result.top_candidates) <= 10
    valid_pairs = set(predictor._build_rule_based_pairs(int(match_id)))
    assert valid_pairs

    for candidate in result.top_candidates:
        assert (candidate.home_team, candidate.away_team) in valid_pairs
        assert candidate.home_team != candidate.away_team
        assert candidate.score > 0
        assert "scenario-search simulation" in candidate.reason


def test_predict_knockout_candidates_are_probability_like(fast_predictor: MatchupPredictor) -> None:
    predictor = fast_predictor
    result = predictor.predict("82")
    total = sum(candidate.score for candidate in result.top_candidates)
    assert 0.95 <= total <= 1.05
    assert result.top_candidates == sorted(result.top_candidates, key=lambda c: c.score, reverse=True)


def test_predict_is_deterministic_for_world_ranking_path(fast_predictor: MatchupPredictor) -> None:
    predictor = fast_predictor
    result_a = predictor.predict("82")
    result_b = predictor.predict("82")
    pairs_a = [(candidate.home_team, candidate.away_team, candidate.score) for candidate in result_a.top_candidates]
    pairs_b = [(candidate.home_team, candidate.away_team, candidate.score) for candidate in result_b.top_candidates]
    assert pairs_a == pairs_b


def test_build_rule_based_pairs_narrows_with_played_results_state() -> None:
    base_predictor = MatchupPredictor()
    world = base_predictor._load_world_cup_data()
    fifa = base_predictor._load_fifa_ranking_data()

    class StubDataProvider(MatchDataProvider):
        def load_matches(self) -> dict:
            return {
                "stub-74": {
                    "label": "Round of 32 - Match 74",
                    "status": "completed",
                    "confirmed_home": "Germany",
                    "confirmed_away": "Sweden",
                    "home_goals": 2,
                    "away_goals": 1,
                }
            }

        def load_world_cup_data(self) -> dict:
            return world

        def load_fifa_ranking_data(self) -> dict:
            return fifa

        def load_participant_teams(self) -> list[str]:
            participants = world.get("participants", [])
            return [p["name"] for p in participants if p.get("name")]

    predictor = MatchupPredictor(data_provider=StubDataProvider())
    pairs = predictor._build_rule_based_pairs(89)

    assert pairs
    assert all(home == "Germany" for home, _away in pairs)


def test_live_results_are_merged_into_match_results_state() -> None:
    base_predictor = MatchupPredictor()
    world = base_predictor._load_world_cup_data()
    fifa = base_predictor._load_fifa_ranking_data()

    class StubDataProvider(MatchDataProvider):
        def load_matches(self) -> dict:
            return {}

        def load_live_results(self) -> dict:
            return {
                "results": {
                    "1": {
                        "match_number": 1,
                        "status": "completed",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "home_goals": 2,
                        "away_goals": 1,
                    }
                }
            }

        def load_world_cup_data(self) -> dict:
            return world

        def load_fifa_ranking_data(self) -> dict:
            return fifa

        def load_participant_teams(self) -> list[str]:
            return [p["name"] for p in world.get("participants", []) if p.get("name")]

    result = MatchupPredictor(data_provider=StubDataProvider())._load_match_results_state()[1]

    assert result.played is True
    assert result.home_team == "Mexico"
    assert result.away_team == "South Africa"
    assert result.home_goals == 2
    assert result.away_goals == 1


def test_live_result_snapshot_narrows_rule_based_pairs() -> None:
    base_predictor = MatchupPredictor()
    world = base_predictor._load_world_cup_data()
    fifa = base_predictor._load_fifa_ranking_data()

    class StubDataProvider(MatchDataProvider):
        def load_matches(self) -> dict:
            return {}

        def load_live_results(self) -> dict:
            return {
                "results": {
                    "74": {
                        "match_number": 74,
                        "status": "completed",
                        "home_team": "Germany",
                        "away_team": "Sweden",
                        "home_goals": 2,
                        "away_goals": 1,
                    }
                }
            }

        def load_world_cup_data(self) -> dict:
            return world

        def load_fifa_ranking_data(self) -> dict:
            return fifa

        def load_participant_teams(self) -> list[str]:
            return [p["name"] for p in world.get("participants", []) if p.get("name")]

    pairs = MatchupPredictor(data_provider=StubDataProvider())._build_rule_based_pairs(89)

    assert pairs
    assert all(home == "Germany" for home, _away in pairs)


def test_manual_result_overrides_live_result_state() -> None:
    base_predictor = MatchupPredictor()
    world = base_predictor._load_world_cup_data()
    fifa = base_predictor._load_fifa_ranking_data()

    class StubDataProvider(MatchDataProvider):
        def load_matches(self) -> dict:
            return {
                "manual-1": {
                    "match_number": 1,
                    "status": "completed",
                    "confirmed_home": "Mexico",
                    "confirmed_away": "South Africa",
                    "home_goals": 0,
                    "away_goals": 0,
                }
            }

        def load_live_results(self) -> dict:
            return {
                "results": {
                    "1": {
                        "match_number": 1,
                        "status": "completed",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "home_goals": 2,
                        "away_goals": 1,
                    }
                }
            }

        def load_world_cup_data(self) -> dict:
            return world

        def load_fifa_ranking_data(self) -> dict:
            return fifa

        def load_participant_teams(self) -> list[str]:
            return [p["name"] for p in world.get("participants", []) if p.get("name")]

    result = MatchupPredictor(data_provider=StubDataProvider())._load_match_results_state()[1]

    assert result.home_goals == 0
    assert result.away_goals == 0


def test_malformed_live_snapshot_does_not_crash_match_result_loading() -> None:
    base_predictor = MatchupPredictor()
    world = base_predictor._load_world_cup_data()
    fifa = base_predictor._load_fifa_ranking_data()

    class StubDataProvider(MatchDataProvider):
        def load_matches(self) -> dict:
            return {}

        def load_live_results(self) -> dict:
            raise ValueError("malformed live snapshot")

        def load_world_cup_data(self) -> dict:
            return world

        def load_fifa_ranking_data(self) -> dict:
            return fifa

        def load_participant_teams(self) -> list[str]:
            return [p["name"] for p in world.get("participants", []) if p.get("name")]

    assert MatchupPredictor(data_provider=StubDataProvider())._load_match_results_state() == {}


class ExplodingSnapshotFetcher:
    calls = 0

    def fetch_snapshot(self):
        self.calls += 1
        raise RuntimeError("market should not be fetched")


def test_strength_mode_factory_selects_fifa_without_market_access() -> None:
    fetcher = ExplodingSnapshotFetcher()
    store = PolymarketSnapshotStore(
        fetcher=fetcher,
        cache_path=None,
        auto_refresh_on_access=False,
    )
    factory = WorldRankingSimulatorFactory(
        config=PredictorRuntimeConfig(strength_mode="fifa"),
        polymarket_snapshot_store=store,
    )

    model = factory.create_default_pairwise_win_model(canonical_team_names=["Mexico", "South Africa"])

    assert isinstance(model, RatingPairwiseWinModel)
    assert fetcher.calls == 0
    assert factory.polymarket_snapshot_status(["Mexico", "South Africa"]) is None


@pytest.mark.parametrize("mode", ["hybrid", "market"])
def test_strength_mode_factory_selects_market_backed_pairwise_model(mode: str) -> None:
    store = PolymarketSnapshotStore(
        fetcher=ExplodingSnapshotFetcher(),
        cache_path=None,
        auto_refresh_on_access=False,
    )
    factory = WorldRankingSimulatorFactory(
        config=PredictorRuntimeConfig(strength_mode=mode),  # type: ignore[arg-type]
        polymarket_snapshot_store=store,
    )

    model = factory.create_default_pairwise_win_model(canonical_team_names=["Mexico", "South Africa"])

    assert isinstance(model, HybridPairwiseWinModel)
    assert model.mode_label == mode


def test_market_mode_prediction_marks_visible_fifa_fallback() -> None:
    store = PolymarketSnapshotStore(
        fetcher=ExplodingSnapshotFetcher(),
        cache_path=None,
        auto_refresh_on_access=False,
    )
    predictor = MatchupPredictor(
        simulator_factory=FastTestWorldRankingSimulatorFactory(
            config=PredictorRuntimeConfig(strength_mode="market"),
            polymarket_snapshot_store=store,
        )
    )

    result = predictor.predict("1")

    assert result.signal_status is not None
    assert result.signal_status["strength_mode"] == "market"
    assert result.signal_status["fallback_visible"] is True
    assert result.signal_status["fallback_hits"] > 0
    assert "FIFA fallback" in result.top_candidates[0].reason
