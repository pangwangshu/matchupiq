from __future__ import annotations

from src.engine import MatchupPredictor
from src.tournament import MatchResultState, TournamentStructureResolver


def _resolver() -> TournamentStructureResolver:
    predictor = MatchupPredictor()
    world = predictor._load_world_cup_data()
    return TournamentStructureResolver(
        groups=world["groups"],
        schedule=world["schedule"],
    )


def test_parse_fixed_matchup() -> None:
    resolver = _resolver()
    assert resolver.parse_fixed_matchup("Team A vs Team B") == ("Team A", "Team B")
    assert resolver.parse_fixed_matchup("invalid") is None


def test_expand_groups_and_parse_group_slot() -> None:
    resolver = _resolver()
    assert resolver.expand_groups("A/B/C") == ["A", "B", "C"]
    assert resolver.parse_group_slot("Group E winners") == ({"E"}, "winners")
    assert resolver.parse_group_slot("Group L runners-up") == ({"L"}, "runners-up")
    assert resolver.parse_group_slot("Group A/B/C third place") == ({"A", "B", "C"}, "third place")
    assert resolver.parse_group_slot("Group A fourth place") is None


def test_teams_from_groups() -> None:
    resolver = _resolver()
    teams = resolver.teams_from_groups(["A"])
    assert len(teams) == 4
    assert "Mexico" in teams


def test_resolve_slot_teams_group_and_missing_dependency() -> None:
    resolver = _resolver()
    from_group = resolver.resolve_slot_teams("Group E winners")
    assert len(from_group) == 4
    assert "Germany" in from_group

    missing = resolver.resolve_slot_teams("Winner Match 9999")
    assert missing == set()


def test_resolve_match_team_pool_for_winner_dependency() -> None:
    resolver = _resolver()
    pool_74 = resolver.resolve_match_team_pool(74)
    assert pool_74

    groups = {g["group"].replace("Group", "").strip(): set(g["countries"]) for g in resolver.groups}
    expected = groups["E"] | groups["A"] | groups["B"] | groups["C"] | groups["D"] | groups["F"]
    assert pool_74 == expected


def test_build_rule_based_pairs_for_round_of_32() -> None:
    resolver = _resolver()
    pairs = resolver.build_rule_based_pairs(74)
    assert pairs

    groups = {g["group"].replace("Group", "").strip(): set(g["countries"]) for g in resolver.groups}
    left_expected = groups["E"]
    right_expected = groups["A"] | groups["B"] | groups["C"] | groups["D"] | groups["F"]

    for home, away in pairs:
        assert home in left_expected
        assert away in right_expected
        assert home != away


def test_build_rule_based_pairs_for_winner_winner_dependency() -> None:
    resolver = _resolver()
    pairs = resolver.build_rule_based_pairs(89)
    assert pairs

    valid_74 = resolver.resolve_match_team_pool(74)
    valid_77 = resolver.resolve_match_team_pool(77)
    for home, away in pairs:
        assert home in valid_74
        assert away in valid_77
        assert home != away


def test_resolve_winner_and_loser_slot_uses_played_result() -> None:
    resolver = _resolver()
    match_results = {
        74: MatchResultState(
            played=True,
            home_team="Germany",
            away_team="Sweden",
            home_goals=2,
            away_goals=1,
        )
    }

    winner = resolver.resolve_slot_teams("Winner Match 74", match_results=match_results)
    loser = resolver.resolve_slot_teams("Loser Match 74", match_results=match_results)

    assert winner == {"Germany"}
    assert loser == {"Sweden"}


def test_build_rule_based_pairs_uses_resolved_winner_team_when_available() -> None:
    resolver = _resolver()
    match_results = {
        74: MatchResultState(
            played=True,
            home_team="Germany",
            away_team="Sweden",
            home_goals=2,
            away_goals=1,
        )
    }

    pairs = resolver.build_rule_based_pairs(89, match_results=match_results)
    assert pairs
    assert all(home == "Germany" for home, _away in pairs)


def test_group_slots_collapse_to_ranked_teams_when_group_completed() -> None:
    resolver = _resolver()
    match_results = {
        1: MatchResultState(
            played=True,
            home_team="Mexico",
            away_team="South Africa",
            home_goals=1,
            away_goals=0,
        ),
        2: MatchResultState(
            played=True,
            home_team="South Korea",
            away_team="Czech Republic",
            home_goals=0,
            away_goals=0,
        ),
        25: MatchResultState(
            played=True,
            home_team="Czech Republic",
            away_team="South Africa",
            home_goals=0,
            away_goals=2,
        ),
        28: MatchResultState(
            played=True,
            home_team="Mexico",
            away_team="South Korea",
            home_goals=2,
            away_goals=0,
        ),
        53: MatchResultState(
            played=True,
            home_team="Czech Republic",
            away_team="Mexico",
            home_goals=1,
            away_goals=1,
        ),
        54: MatchResultState(
            played=True,
            home_team="South Africa",
            away_team="South Korea",
            home_goals=1,
            away_goals=2,
        ),
    }

    winners = resolver.resolve_slot_teams("Group A winners", match_results=match_results)
    runners_up = resolver.resolve_slot_teams("Group A runners-up", match_results=match_results)
    third_place = resolver.resolve_slot_teams("Group A third place", match_results=match_results)

    assert winners == {"Mexico"}
    assert runners_up == {"South Korea"}
    assert third_place == {"South Africa"}
