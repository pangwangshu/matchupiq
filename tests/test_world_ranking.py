from __future__ import annotations

from src.engine import MatchupPredictor
from src.world_ranking import WorldRankingTournamentSimulator


def _simulator() -> WorldRankingTournamentSimulator:
    predictor = MatchupPredictor()
    return WorldRankingTournamentSimulator(
        world_cup_data=predictor._load_world_cup_data(),
        fifa_ranking_data=predictor._load_fifa_ranking_data(),
    )


def test_simulate_tournament_builds_knockout_matches() -> None:
    simulator = _simulator()
    simulated = simulator.simulate_tournament()
    assert 74 in simulated
    assert 89 in simulated
    assert 104 in simulated
    assert simulated[104][0] != simulated[104][1]


def test_simulation_is_deterministic() -> None:
    simulator = _simulator()
    first = simulator.simulate_tournament()
    second = simulator.simulate_tournament()
    assert first == second


def test_third_place_assignments_use_top_eight_without_reuse() -> None:
    simulator = _simulator()
    group_standings, third_place_rows = simulator._simulate_group_stage()
    top_eight_third = {standing.team for _group, standing in third_place_rows[:8]}
    simulated = simulator.simulate_tournament()

    third_place_selected: list[str] = []
    schedule = simulator.world_cup_data["schedule"]
    for match in schedule:
        if match.get("stage") != "round_of_32":
            continue
        slots = simulator.resolver.parse_fixed_matchup(str(match.get("comment") or match.get("matchup") or ""))
        if not slots:
            continue
        home_slot, away_slot = slots
        pair = simulated[int(match["match_number"])]
        home_team, away_team = pair

        parsed_home = simulator._parse_group_slot_with_order(home_slot)
        parsed_away = simulator._parse_group_slot_with_order(away_slot)
        if parsed_home and parsed_home[1] == "third place":
            third_place_selected.append(home_team)
        if parsed_away and parsed_away[1] == "third place":
            third_place_selected.append(away_team)

    assert len(third_place_selected) == 8
    assert len(set(third_place_selected)) == 8
    assert set(third_place_selected).issubset(top_eight_third)
    assert group_standings

