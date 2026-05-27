from __future__ import annotations

from src.engine import MatchupPredictor
from src.tournament import MatchResultState
from src.world_ranking import WorldRankingTournamentSimulator


def _simulator() -> WorldRankingTournamentSimulator:
    predictor = MatchupPredictor()
    return WorldRankingTournamentSimulator(
        world_cup_data=predictor._load_world_cup_data(),
        fifa_ranking_data=predictor._load_fifa_ranking_data(),
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

    baseline_simulator = WorldRankingTournamentSimulator(
        world_cup_data=world_cup_data,
        fifa_ranking_data=fifa_ranking_data,
    )
    baseline = baseline_simulator.predict_matchup_candidates(match_number=89, limit=10)
    assert baseline
    assert len({home for home, _away, _score in baseline}) > 1

    constrained_simulator = WorldRankingTournamentSimulator(
        world_cup_data=world_cup_data,
        fifa_ranking_data=fifa_ranking_data,
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
