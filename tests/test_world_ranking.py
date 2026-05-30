from __future__ import annotations

import pytest

from src.engine import MatchupPredictor
from src.tournament import MatchResultState
from src.world_ranking import MatchContext, MatchOutcome, FifaTeamPowerModel, WorldRankingTournamentSimulator


def _simulator() -> WorldRankingTournamentSimulator:
    predictor = MatchupPredictor()
    team_power_model = FifaTeamPowerModel(
        fifa_ranking_data=predictor._load_fifa_ranking_data(),
        default_rank_for_unlisted_team=120,
        default_points_fallback=1400.0,
    )
    return WorldRankingTournamentSimulator(
        world_cup_data=predictor._load_world_cup_data(),
        team_power_model=team_power_model,
    )


def test_predict_matchup_candidates_returns_ranked_probabilities() -> None:
    simulator = _simulator()
    candidates = simulator.predict_matchup_candidates(match_number=82, limit=10)
    assert candidates
    assert len(candidates) <= 10
    assert candidates == sorted(candidates, key=lambda item: item[2], reverse=True)
    total = sum(probability for _home, _away, probability in candidates)
    assert 0.95 <= total <= 1.05
    for home, away, probability in candidates:
        assert home != away
        assert probability > 0


def test_predict_matchup_candidates_respects_rule_space() -> None:
    simulator = _simulator()
    predictor = MatchupPredictor()
    valid_pairs = set(predictor._build_rule_based_pairs(82))
    assert valid_pairs

    candidates = simulator.predict_matchup_candidates(match_number=82, limit=10)
    assert candidates
    for home, away, _probability in candidates:
        assert (home, away) in valid_pairs


def test_world_beam_search_is_deterministic() -> None:
    simulator = _simulator()
    first = simulator.predict_matchup_candidates(match_number=94, limit=10)
    second = simulator.predict_matchup_candidates(match_number=94, limit=10)
    assert first == second


def test_world_scenarios_are_bounded_and_nonempty() -> None:
    simulator = _simulator()
    worlds = simulator._beam_world_scenarios(
        group_beam_width=32,
        group_max_scenarios=8,
        world_beam_width=64,
        min_branch_probability=1e-5,
    )
    assert worlds
    assert len(worlds) <= 64
    total = sum(world.probability for world in worlds)
    assert 0.95 <= total <= 1.05


def test_simulate_tournament_back_compat_has_knockout_rows(monkeypatch) -> None:
    simulator = _simulator()
    
    def _stub_predict(match_number: int, limit: int = 10, **_kwargs):
        return [(f"Home {match_number}", f"Away {match_number}", 1.0)]

    monkeypatch.setattr(simulator, "predict_matchup_candidates", _stub_predict)
    simulated = simulator.simulate_tournament(match_numbers=[74, 89, 104])
    assert 74 in simulated
    assert 89 in simulated
    assert 104 in simulated
    assert simulated[104][0] != simulated[104][1]


def test_completed_knockout_match_narrows_candidate_space() -> None:
    predictor = MatchupPredictor()
    world_cup_data = predictor._load_world_cup_data()
    fifa_ranking_data = predictor._load_fifa_ranking_data()
    team_power_model = FifaTeamPowerModel(
        fifa_ranking_data=fifa_ranking_data,
        default_rank_for_unlisted_team=120,
        default_points_fallback=1400.0,
    )

    baseline_simulator = WorldRankingTournamentSimulator(
        world_cup_data=world_cup_data,
        team_power_model=team_power_model,
    )
    baseline = baseline_simulator.predict_matchup_candidates(match_number=89, limit=10)
    assert baseline
    assert len({home for home, _away, _score in baseline}) > 1

    constrained_simulator = WorldRankingTournamentSimulator(
        world_cup_data=world_cup_data,
        team_power_model=team_power_model,
        match_results={
            74: MatchResultState(
                played=True,
                home_team="Germany",
                away_team="Sweden",
                home_goals=2,
                away_goals=1,
            )
        },
    )
    constrained = constrained_simulator.predict_matchup_candidates(match_number=89, limit=10)
    assert constrained
    assert {home for home, _away, _score in constrained} == {"Germany"}


def test_simulator_construction_without_fifa_payload_is_supported_and_deterministic() -> None:
    class FixedTeamPowerModel:
        def team_rating(self, team: str) -> float:
            return 1500.0

        def team_rank(self, team: str) -> int:
            return 100

    class FixedPairwiseWinModel:
        def __init__(self) -> None:
            self.group_calls = 0
            self.knockout_calls = 0
            self.seen_group_contexts: list[MatchContext] = []
            self.seen_knockout_contexts: list[MatchContext] = []

        def group_outcomes(
            self,
            home_team: str,
            away_team: str,
            match_context: MatchContext,
            *,
            team_power_model,
            model_config,
            decisive_band: float,
        ):
            self.group_calls += 1
            self.seen_group_contexts.append(match_context)
            _ = (home_team, away_team, team_power_model, model_config, decisive_band)
            return [
                MatchOutcome(home_goals=2, away_goals=1, probability=0.35),
                MatchOutcome(home_goals=1, away_goals=1, probability=0.30),
                MatchOutcome(home_goals=1, away_goals=2, probability=0.35),
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
            self.knockout_calls += 1
            self.seen_knockout_contexts.append(match_context)
            _ = (home_team, away_team, team_power_model, model_config, draw_band)
            return 0.5

    predictor = MatchupPredictor()
    team_power_model = FixedTeamPowerModel()
    pairwise_win_model = FixedPairwiseWinModel()
    simulator = WorldRankingTournamentSimulator(
        world_cup_data=predictor._load_world_cup_data(),
        team_power_model=team_power_model,
        pairwise_win_model=pairwise_win_model,
    )

    first = simulator.predict_matchup_candidates(match_number=82, limit=10)
    second = simulator.predict_matchup_candidates(match_number=82, limit=10)
    final_candidates = simulator.predict_matchup_candidates(match_number=104, limit=10)

    assert first
    assert first == second
    assert final_candidates
    assert pairwise_win_model.group_calls > 0
    assert pairwise_win_model.knockout_calls > 0
    assert any(ctx.match_number is not None for ctx in pairwise_win_model.seen_group_contexts)
    assert any(ctx.stage == "group_stage" for ctx in pairwise_win_model.seen_group_contexts)
    assert any(ctx.date is not None for ctx in pairwise_win_model.seen_group_contexts)
    assert any(ctx.match_number is not None for ctx in pairwise_win_model.seen_knockout_contexts)
    assert any(ctx.date is not None for ctx in pairwise_win_model.seen_knockout_contexts)


def test_group_outcome_probability_contract_rejects_non_normalized_distribution() -> None:
    class FixedTeamPowerModel:
        def team_rating(self, team: str) -> float:
            return 1500.0

        def team_rank(self, team: str) -> int:
            return 100

    class InvalidGroupPairwiseWinModel:
        def group_outcomes(
            self,
            home_team: str,
            away_team: str,
            match_context: MatchContext,
            *,
            team_power_model,
            model_config,
            decisive_band: float,
        ):
            _ = (home_team, away_team, match_context, team_power_model, model_config, decisive_band)
            return [
                MatchOutcome(home_goals=2, away_goals=1, probability=0.5),
                MatchOutcome(home_goals=1, away_goals=1, probability=0.5),
                MatchOutcome(home_goals=1, away_goals=2, probability=0.2),
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
            return 0.5

    predictor = MatchupPredictor()
    simulator = WorldRankingTournamentSimulator(
        world_cup_data=predictor._load_world_cup_data(),
        team_power_model=FixedTeamPowerModel(),
        pairwise_win_model=InvalidGroupPairwiseWinModel(),
    )

    with pytest.raises(ValueError, match="must sum to 1.0"):
        simulator.predict_matchup_candidates(match_number=82, limit=10)


def test_knockout_probability_contract_clamps_to_closed_interval() -> None:
    class FixedTeamPowerModel:
        def team_rating(self, team: str) -> float:
            return 1500.0

        def team_rank(self, team: str) -> int:
            return 100

    class OverconfidentPairwiseWinModel:
        def group_outcomes(
            self,
            home_team: str,
            away_team: str,
            match_context: MatchContext,
            *,
            team_power_model,
            model_config,
            decisive_band: float,
        ):
            _ = (home_team, away_team, match_context, team_power_model, model_config, decisive_band)
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
            return 1.4

    predictor = MatchupPredictor()
    simulator = WorldRankingTournamentSimulator(
        world_cup_data=predictor._load_world_cup_data(),
        team_power_model=FixedTeamPowerModel(),
        pairwise_win_model=OverconfidentPairwiseWinModel(),
    )

    assert simulator._knockout_home_win_probability("A", "B", match_number=104) == 1.0
