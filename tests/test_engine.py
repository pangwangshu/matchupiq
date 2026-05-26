from __future__ import annotations

import pytest

from src.engine import MatchupPredictor


def test_parse_group_slot_covers_winners_runners_up_and_third_place() -> None:
    predictor = MatchupPredictor()

    winners = predictor._parse_group_slot("Group E winners")
    assert winners == ({"E"}, "winners")

    runners_up = predictor._parse_group_slot("Group L runners-up")
    assert runners_up == ({"L"}, "runners-up")

    third_place_multi = predictor._parse_group_slot("Group A/B/C third place")
    assert third_place_multi == ({"A", "B", "C"}, "third place")


def test_predict_non_numeric_match_id_falls_back_to_baseline() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("400021525")

    assert result.status == "predicted"
    assert len(result.top_candidates) == 10
    assert all(
        c.reason == "Baseline UI test: matchup can be any two different participant teams."
        for c in result.top_candidates
    )


def test_predict_group_stage_match_has_multiple_candidates() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("1")

    assert result.status == "predicted"
    assert 1 <= len(result.top_candidates) <= 10
    assert "FIFA world-ranking scenario search" in result.top_candidates[0].reason

    # Group-stage match is fixed by schedule, so top pair should be the scheduled one.
    top = result.top_candidates[0]
    assert (top.home_team, top.away_team) == ("Mexico", "South Africa")
    assert top.score > 0


@pytest.mark.parametrize("match_id", ["74", "80", "82", "89", "94", "103", "104"])
def test_predict_knockout_candidates_respect_rule_space(match_id: str) -> None:
    predictor = MatchupPredictor()
    result = predictor.predict(match_id)

    assert result.status == "predicted"
    assert 1 <= len(result.top_candidates) <= 10
    valid_pairs = set(predictor._build_rule_based_pairs(int(match_id)))
    assert valid_pairs

    for candidate in result.top_candidates:
        assert (candidate.home_team, candidate.away_team) in valid_pairs
        assert candidate.home_team != candidate.away_team
        assert candidate.score > 0
        assert "FIFA world-ranking scenario search" in candidate.reason


def test_predict_knockout_candidates_are_probability_like() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("82")
    total = sum(candidate.score for candidate in result.top_candidates)
    assert 0.95 <= total <= 1.05
    assert result.top_candidates == sorted(result.top_candidates, key=lambda c: c.score, reverse=True)


def test_predict_is_deterministic_for_world_ranking_path() -> None:
    predictor = MatchupPredictor()
    result_a = predictor.predict("82")
    result_b = predictor.predict("82")
    pairs_a = [(candidate.home_team, candidate.away_team, candidate.score) for candidate in result_a.top_candidates]
    pairs_b = [(candidate.home_team, candidate.away_team, candidate.score) for candidate in result_b.top_candidates]
    assert pairs_a == pairs_b

