from __future__ import annotations

import pytest

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


def test_predict_group_winner_vs_third_place_rule_constrained_additional_match() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("80")

    assert result.status == "predicted"
    assert len(result.top_candidates) == 10

    groups = _group_map()
    left_expected = groups["L"]
    right_expected = (
        groups["E"] | groups["H"] | groups["I"] | groups["J"] | groups["K"]
    )

    for candidate in result.top_candidates:
        assert candidate.home_team in left_expected
        assert candidate.away_team in right_expected
        assert candidate.home_team != candidate.away_team
        assert candidate.reason == "Rule-constrained baseline candidate."


@pytest.mark.parametrize("match_id", ["89", "104"])
def test_predict_winner_dependency_rule_constrained(match_id: str) -> None:
    predictor = MatchupPredictor()
    result = predictor.predict(match_id)

    assert result.status == "predicted"
    assert len(result.top_candidates) == 10

    valid_pairs = set(predictor._build_rule_based_pairs(int(match_id)))
    assert valid_pairs

    for candidate in result.top_candidates:
        assert (candidate.home_team, candidate.away_team) in valid_pairs
        assert candidate.home_team != candidate.away_team
        assert candidate.reason == "Rule-constrained baseline candidate."


def test_predict_loser_dependency_rule_constrained_third_place_match() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("103")

    assert result.status == "predicted"
    assert len(result.top_candidates) == 10

    valid_pairs = set(predictor._build_rule_based_pairs(103))
    assert valid_pairs

    for candidate in result.top_candidates:
        assert (candidate.home_team, candidate.away_team) in valid_pairs
        assert candidate.home_team != candidate.away_team
        assert candidate.reason == "Rule-constrained baseline candidate."


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


@pytest.mark.parametrize("match_id", ["82", "94"])
def test_predict_candidates_for_82_and_94_are_possible(match_id: str) -> None:
    predictor = MatchupPredictor()
    valid_pairs = set(predictor._build_rule_based_pairs(int(match_id)))
    assert valid_pairs

    for _ in range(5):
        result = predictor.predict(match_id)
        assert len(result.top_candidates) == 10
        for c in result.top_candidates:
            assert (c.home_team, c.away_team) in valid_pairs


def test_predict_is_deterministic_with_fixed_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    import random

    predictor = MatchupPredictor()
    random.seed(2026)
    result_a = predictor.predict("82")
    pairs_a = [(c.home_team, c.away_team) for c in result_a.top_candidates]

    random.seed(2026)
    result_b = predictor.predict("82")
    pairs_b = [(c.home_team, c.away_team) for c in result_b.top_candidates]

    assert pairs_a == pairs_b


def test_predict_has_no_duplicate_unordered_pairs() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("94")
    unordered = [tuple(sorted((c.home_team, c.away_team))) for c in result.top_candidates]
    assert len(unordered) == len(set(unordered))


def test_rule_resolution_missing_match_dependency_returns_empty() -> None:
    predictor = MatchupPredictor()
    world = predictor._load_world_cup_data()
    schedule = world["schedule"]
    groups = world["groups"]
    group_to_teams = {
        g["group"].replace("Group", "").strip(): list(g["countries"])
        for g in groups
    }
    schedule_by_number = {
        int(m["match_number"]): m
        for m in schedule
        if isinstance(m.get("match_number"), int)
    }
    out = predictor._resolve_slot_teams(
        "Winner Match 9999",
        schedule_by_number,
        group_to_teams,
        cache={},
        stack=set(),
    )
    assert out == set()


@pytest.mark.xfail(
    reason=(
        "Current model intentionally treats Winner Match N as any team that could appear "
        "in match N; strict left-vs-right bracket lineage is not implemented yet."
    ),
    strict=False,
)
def test_winner_dependency_strict_lineage_future_behavior() -> None:
    predictor = MatchupPredictor()
    result = predictor.predict("89")
    left_from_74_left_slot = _group_map()["E"]
    right_from_77_left_slot = _group_map()["I"]

    # Future stricter expectation example:
    # left side should come from match 74's left slot (Group E winners)
    # right side should come from match 77's left slot (Group I winners)
    for c in result.top_candidates:
        assert c.home_team in left_from_74_left_slot
        assert c.away_team in right_from_77_left_slot
