from __future__ import annotations

from src.engine import MatchupPredictor


def _group_map() -> dict[str, set[str]]:
    predictor = MatchupPredictor()
    world_cup = predictor._load_world_cup_data()
    out: dict[str, set[str]] = {}
    for g in world_cup["groups"]:
        letter = g["group"].replace("Group", "").strip()
        out[letter] = set(g["countries"])
    return out


def test_predict_round_of_32_rule_constrained() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("74")

    assert result.status == "predicted"
    assert len(result.top_candidates) == 10

    groups = _group_map()
    left_expected = groups["E"]
    right_expected = (
        groups["A"] | groups["B"] | groups["C"] | groups["D"] | groups["F"]
    )

    for candidate in result.top_candidates:
        assert candidate.home_team in left_expected
        assert candidate.away_team in right_expected
        assert candidate.home_team != candidate.away_team
        assert candidate.reason == "Rule-constrained baseline candidate."


def test_predict_winner_dependency_rule_constrained() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("89")

    assert result.status == "predicted"
    assert len(result.top_candidates) == 10

    valid_pairs = set(predictor._build_rule_based_pairs(89))
    assert valid_pairs

    for candidate in result.top_candidates:
        assert (candidate.home_team, candidate.away_team) in valid_pairs
        assert candidate.home_team != candidate.away_team
        assert candidate.reason == "Rule-constrained baseline candidate."


def test_predict_non_numeric_match_id_falls_back_to_baseline() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("400021525")

    assert result.status == "predicted"
    assert len(result.top_candidates) == 10
    assert all(
        c.reason == "Baseline UI test: matchup can be any two different participant teams."
        for c in result.top_candidates
    )
