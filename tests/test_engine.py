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


def test_predict_group_stage_match_uses_scheduled_pairing() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("1")

    assert result.status == "predicted"
    assert len(result.top_candidates) == 1
    candidate = result.top_candidates[0]
    assert candidate.home_team == "Mexico"
    assert candidate.away_team == "South Africa"
    assert "FIFA world-ranking simulation" in candidate.reason


@pytest.mark.parametrize("match_id", ["74", "80", "82", "89", "94", "103", "104"])
def test_predict_knockout_match_respects_bracket_rule_space(match_id: str) -> None:
    predictor = MatchupPredictor()
    result = predictor.predict(match_id)

    assert result.status == "predicted"
    assert len(result.top_candidates) == 1
    candidate = result.top_candidates[0]
    valid_pairs = set(predictor._build_rule_based_pairs(int(match_id)))
    assert valid_pairs
    assert (candidate.home_team, candidate.away_team) in valid_pairs
    assert candidate.home_team != candidate.away_team
    assert "FIFA world-ranking simulation" in candidate.reason


def test_predict_is_deterministic_for_world_ranking_path() -> None:
    predictor = MatchupPredictor()
    result_a = predictor.predict("82")
    result_b = predictor.predict("82")
    pair_a = (result_a.top_candidates[0].home_team, result_a.top_candidates[0].away_team)
    pair_b = (result_b.top_candidates[0].home_team, result_b.top_candidates[0].away_team)
    assert pair_a == pair_b

