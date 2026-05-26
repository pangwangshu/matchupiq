from __future__ import annotations

import re
from dataclasses import dataclass

try:
    from src.tournament import TournamentStructureResolver
except ModuleNotFoundError:
    from tournament import TournamentStructureResolver


@dataclass(frozen=True)
class TeamRanking:
    team: str
    fifa_points: float
    fifa_rank: int


@dataclass
class TeamStanding:
    team: str
    points: int = 0
    goals_for: int = 0
    goals_against: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


class WorldRankingTournamentSimulator:
    def __init__(
        self,
        world_cup_data: dict,
        fifa_ranking_data: dict,
        draw_band: float = 20.0,
        decisive_band: float = 80.0,
    ) -> None:
        self.world_cup_data = world_cup_data
        self.draw_band = draw_band
        self.decisive_band = decisive_band
        self.resolver = TournamentStructureResolver(
            groups=world_cup_data.get("groups", []),
            schedule=world_cup_data.get("schedule", []),
        )
        self.rankings_by_team = self._build_ranking_map(fifa_ranking_data)
        all_points = sorted([entry.fifa_points for entry in self.rankings_by_team.values()])
        self.default_points = all_points[len(all_points) // 2] if all_points else 1400.0
        self.default_rank = 120

    def _build_ranking_map(self, fifa_ranking_data: dict) -> dict[str, TeamRanking]:
        out: dict[str, TeamRanking] = {}
        for row in fifa_ranking_data.get("participants_rankings", []):
            team = str(row.get("participant_name", "")).strip()
            if not team:
                continue
            out[team] = TeamRanking(
                team=team,
                fifa_points=float(row.get("points", 0.0)),
                fifa_rank=int(row.get("rank", 999)),
            )
        return out

    def _team_ranking(self, team: str) -> TeamRanking:
        return self.rankings_by_team.get(
            team,
            TeamRanking(team=team, fifa_points=self.default_points, fifa_rank=self.default_rank),
        )

    def _pairwise_strength_diff(self, team_a: str, team_b: str) -> float:
        return self._team_ranking(team_a).fifa_points - self._team_ranking(team_b).fifa_points

    def _simulate_group_score(self, team_a: str, team_b: str) -> tuple[int, int]:
        diff = self._pairwise_strength_diff(team_a, team_b)
        if abs(diff) <= self.draw_band:
            return 1, 1

        margin = 2 if abs(diff) >= self.decisive_band else 1
        if diff > 0:
            return 1 + margin, 1
        return 1, 1 + margin

    def _simulate_knockout_winner(self, team_a: str, team_b: str) -> tuple[str, str]:
        diff = self._pairwise_strength_diff(team_a, team_b)
        if abs(diff) <= self.draw_band:
            rank_a = self._team_ranking(team_a).fifa_rank
            rank_b = self._team_ranking(team_b).fifa_rank
            if rank_a <= rank_b:
                return team_a, team_b
            return team_b, team_a

        if diff > 0:
            return team_a, team_b
        return team_b, team_a

    def _standing_sort_key(self, standing: TeamStanding) -> tuple:
        team_rank = self._team_ranking(standing.team)
        return (
            -standing.points,
            -standing.goal_difference,
            -standing.goals_for,
            -team_rank.fifa_points,
            team_rank.fifa_rank,
            standing.team,
        )

    def _init_group_tables(self) -> dict[str, dict[str, TeamStanding]]:
        tables: dict[str, dict[str, TeamStanding]] = {}
        for group in self.world_cup_data.get("groups", []):
            letter = str(group.get("group", "")).replace("Group", "").strip().upper()
            if not letter:
                continue
            tables[letter] = {
                team: TeamStanding(team=team)
                for team in [str(country) for country in group.get("countries", [])]
            }
        return tables

    def _simulate_group_stage(self) -> tuple[dict[str, list[TeamStanding]], list[tuple[str, TeamStanding]]]:
        tables = self._init_group_tables()
        group_matches = sorted(
            [
                match
                for match in self.world_cup_data.get("schedule", [])
                if str(match.get("stage", "")) == "group_stage"
            ],
            key=lambda match: int(match.get("match_number", 0)),
        )

        for match in group_matches:
            group_name = str(match.get("group", ""))
            letter = group_name.replace("Group", "").strip().upper()
            if letter not in tables:
                continue

            matchup_text = str(match.get("matchup") or match.get("comment") or "")
            parsed = self.resolver.parse_fixed_matchup(matchup_text)
            if not parsed:
                continue
            home_team, away_team = parsed
            if home_team not in tables[letter] or away_team not in tables[letter]:
                continue

            home_goals, away_goals = self._simulate_group_score(home_team, away_team)
            home = tables[letter][home_team]
            away = tables[letter][away_team]
            home.goals_for += home_goals
            home.goals_against += away_goals
            away.goals_for += away_goals
            away.goals_against += home_goals

            if home_goals > away_goals:
                home.points += 3
                home.wins += 1
                away.losses += 1
            elif home_goals < away_goals:
                away.points += 3
                away.wins += 1
                home.losses += 1
            else:
                home.points += 1
                away.points += 1
                home.draws += 1
                away.draws += 1

        sorted_group_tables: dict[str, list[TeamStanding]] = {}
        third_place_rows: list[tuple[str, TeamStanding]] = []
        for letter, team_rows in tables.items():
            ordered = sorted(team_rows.values(), key=self._standing_sort_key)
            sorted_group_tables[letter] = ordered
            if len(ordered) >= 3:
                third_place_rows.append((letter, ordered[2]))

        third_place_rows.sort(key=lambda pair: self._standing_sort_key(pair[1]))
        return sorted_group_tables, third_place_rows

    def _parse_group_slot_with_order(self, slot_text: str) -> tuple[list[str], str] | None:
        pattern = r"^Group\s+([A-Z](?:/[A-Z])*)\s+(winners|runners-up|third place)$"
        match = re.match(pattern, slot_text.strip(), flags=re.IGNORECASE)
        if not match:
            return None
        group_letters = self.resolver.expand_groups(match.group(1).upper())
        slot_type = match.group(2).lower()
        return group_letters, slot_type

    def _pick_team_for_group_slot(
        self,
        group_order: list[str],
        slot_type: str,
        group_standings: dict[str, list[TeamStanding]],
    ) -> str:
        if slot_type == "winners":
            options = [group_standings[group][0].team for group in group_order if group in group_standings]
            return self._pick_best_ranked_team(options)
        if slot_type == "runners-up":
            options = [group_standings[group][1].team for group in group_order if group in group_standings]
            return self._pick_best_ranked_team(options)
        raise ValueError(f"Unable to resolve slot type '{slot_type}' for groups {group_order}.")

    def _pick_best_ranked_team(self, teams: list[str]) -> str:
        if not teams:
            raise ValueError("Unable to pick team from empty options.")
        return sorted(
            teams,
            key=lambda team: (
                -self._team_ranking(team).fifa_points,
                self._team_ranking(team).fifa_rank,
                team,
            ),
        )[0]

    def _group_letter_for_team(self, team: str) -> str | None:
        for letter, teams in self.resolver.group_to_teams.items():
            if team in teams:
                return letter
        return None

    def _resolve_knockout_slot_team(
        self,
        slot_text: str,
        match_number: int,
        side: str,
        group_standings: dict[str, list[TeamStanding]],
        third_place_assignments: dict[tuple[int, str], str],
        winners_by_match: dict[int, str],
        losers_by_match: dict[int, str],
    ) -> str:
        slot_text = slot_text.strip()
        group_slot = self._parse_group_slot_with_order(slot_text)
        if group_slot:
            group_order, slot_type = group_slot
            if slot_type == "third place":
                key = (match_number, side)
                if key not in third_place_assignments:
                    raise ValueError(f"Missing third-place assignment for match {match_number} {side}.")
                return third_place_assignments[key]
            return self._pick_team_for_group_slot(group_order, slot_type, group_standings)

        winner_match = re.match(r"^Winner Match\s+(\d+)$", slot_text, flags=re.IGNORECASE)
        if winner_match:
            ref = int(winner_match.group(1))
            if ref not in winners_by_match:
                raise ValueError(f"Winner dependency unresolved for match {ref}.")
            return winners_by_match[ref]

        loser_match = re.match(r"^Loser Match\s+(\d+)$", slot_text, flags=re.IGNORECASE)
        if loser_match:
            ref = int(loser_match.group(1))
            if ref not in losers_by_match:
                raise ValueError(f"Loser dependency unresolved for match {ref}.")
            return losers_by_match[ref]

        return slot_text

    def _collect_third_place_slots(self) -> list[tuple[int, str, set[str]]]:
        slots: list[tuple[int, str, set[str]]] = []
        for match in sorted(
            [
                item
                for item in self.world_cup_data.get("schedule", [])
                if item.get("stage") == "round_of_32" and isinstance(item.get("match_number"), int)
            ],
            key=lambda item: int(item["match_number"]),
        ):
            match_number = int(match["match_number"])
            parsed = self.resolver.parse_fixed_matchup(str(match.get("comment") or match.get("matchup") or ""))
            if not parsed:
                continue
            left_slot, right_slot = parsed
            left_group_slot = self._parse_group_slot_with_order(left_slot)
            right_group_slot = self._parse_group_slot_with_order(right_slot)
            if left_group_slot and left_group_slot[1] == "third place":
                slots.append((match_number, "left", set(left_group_slot[0])))
            if right_group_slot and right_group_slot[1] == "third place":
                slots.append((match_number, "right", set(right_group_slot[0])))
        return slots

    def _assign_third_place_slots(self, best_third_teams: list[str]) -> dict[tuple[int, str], str]:
        slots = self._collect_third_place_slots()
        team_group = {team: self._group_letter_for_team(team) for team in best_third_teams}
        priority = {team: idx for idx, team in enumerate(best_third_teams)}

        options: dict[tuple[int, str], list[str]] = {}
        for match_number, side, allowed_groups in slots:
            key = (match_number, side)
            candidates = [
                team
                for team in best_third_teams
                if team_group.get(team) in allowed_groups
            ]
            candidates.sort(key=lambda team: priority[team])
            options[key] = candidates

        ordered_keys = sorted(options.keys(), key=lambda key: (len(options[key]), key[0], key[1]))
        chosen: dict[tuple[int, str], str] = {}
        used: set[str] = set()

        def backtrack(idx: int) -> bool:
            if idx >= len(ordered_keys):
                return True
            slot_key = ordered_keys[idx]
            for team in options[slot_key]:
                if team in used:
                    continue
                used.add(team)
                chosen[slot_key] = team
                if backtrack(idx + 1):
                    return True
                used.remove(team)
                chosen.pop(slot_key, None)
            return False

        if backtrack(0):
            return chosen

        # Fallback: if strict assignment fails, fill remaining slots greedily.
        remaining = [team for team in best_third_teams if team not in used]
        for slot_key in ordered_keys:
            if slot_key in chosen:
                continue
            valid = [team for team in options[slot_key] if team not in used]
            if valid:
                team = valid[0]
            elif remaining:
                team = remaining.pop(0)
            else:
                raise ValueError("Unable to assign third-place teams to round-of-32 slots.")
            chosen[slot_key] = team
            used.add(team)
        return chosen

    def simulate_tournament(self) -> dict[int, tuple[str, str]]:
        group_standings, third_place_rows = self._simulate_group_stage()
        best_third_teams = [row.team for _group, row in third_place_rows[:8]]
        third_place_assignments = self._assign_third_place_slots(best_third_teams)

        predicted_matchups: dict[int, tuple[str, str]] = {}
        winners_by_match: dict[int, str] = {}
        losers_by_match: dict[int, str] = {}

        schedule = sorted(
            [
                match
                for match in self.world_cup_data.get("schedule", [])
                if isinstance(match.get("match_number"), int)
            ],
            key=lambda match: int(match["match_number"]),
        )

        for match in schedule:
            match_number = int(match["match_number"])
            matchup_text = str(match.get("comment") or match.get("matchup") or "")
            slots = self.resolver.parse_fixed_matchup(matchup_text)
            if not slots:
                continue
            left_slot, right_slot = slots

            stage = str(match.get("stage", ""))
            if stage == "group_stage":
                predicted_matchups[match_number] = (left_slot, right_slot)
                continue

            home_team = self._resolve_knockout_slot_team(
                left_slot,
                match_number,
                "left",
                group_standings,
                third_place_assignments,
                winners_by_match,
                losers_by_match,
            )
            away_team = self._resolve_knockout_slot_team(
                right_slot,
                match_number,
                "right",
                group_standings,
                third_place_assignments,
                winners_by_match,
                losers_by_match,
            )
            predicted_matchups[match_number] = (home_team, away_team)

            winner, loser = self._simulate_knockout_winner(home_team, away_team)
            winners_by_match[match_number] = winner
            losers_by_match[match_number] = loser

        return predicted_matchups
