from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

FIXED_MATCHUP_SPLIT_PATTERN = re.compile(r"\s+vs\s+", flags=re.IGNORECASE)
GROUP_SLOT_PATTERN = re.compile(
    r"^Group\s+([A-Z](?:/[A-Z])*)\s+(winners|runners-up|third place)$",
    flags=re.IGNORECASE,
)
MATCH_REFERENCE_PATTERNS = {
    "winner": re.compile(r"^Winner Match\s+(\d+)$", flags=re.IGNORECASE),
    "loser": re.compile(r"^Loser Match\s+(\d+)$", flags=re.IGNORECASE),
}


def parse_fixed_matchup_text(matchup_text: str) -> tuple[str, str] | None:
    parts = FIXED_MATCHUP_SPLIT_PATTERN.split(str(matchup_text), maxsplit=1)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def expand_group_token(group_token: str) -> list[str]:
    return [group.strip() for group in group_token.split("/") if group.strip()]


def parse_group_slot_text(slot_text: str) -> tuple[list[str], str] | None:
    match = GROUP_SLOT_PATTERN.match(slot_text.strip())
    if not match:
        return None
    groups = expand_group_token(match.group(1).upper())
    slot_type = match.group(2).lower()
    return groups, slot_type


def parse_match_reference(slot_text: str, *, outcome: str) -> int | None:
    pattern = MATCH_REFERENCE_PATTERNS.get(outcome.lower())
    if pattern is None:
        return None
    match = pattern.match(slot_text.strip())
    if not match:
        return None
    return int(match.group(1))


@dataclass(frozen=True)
class MatchResultState:
    """Represents the known state of a scheduled match result."""

    played: bool
    home_team: str | None = None
    away_team: str | None = None
    home_goals: int | None = None
    away_goals: int | None = None


@dataclass
class GroupStandingRow:
    """Stores points and goal totals for a single group-stage team."""

    team: str
    points: int = 0
    goals_for: int = 0
    goals_against: int = 0

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


@dataclass
class TournamentStructureResolver:
    """Resolves valid teams and matchup pools from tournament structure rules."""

    groups: list[dict]
    schedule: list[dict]

    def __post_init__(self) -> None:
        self.group_to_teams: dict[str, list[str]] = {}
        for group in self.groups:
            group_name = str(group.get("group", ""))
            letter = group_name.replace("Group", "").strip().upper()
            if letter:
                self.group_to_teams[letter] = [str(team) for team in group.get("countries", [])]

        self.schedule_by_number = {
            int(match["match_number"]): match
            for match in self.schedule
            if isinstance(match.get("match_number"), int)
        }
        self.group_stage_matches_by_group: dict[str, list[int]] = {}
        for match in self.schedule:
            if match.get("stage") != "group_stage":
                continue
            group_name = str(match.get("group", ""))
            letter = group_name.replace("Group", "").strip().upper()
            match_number = match.get("match_number")
            if not letter or not isinstance(match_number, int):
                continue
            self.group_stage_matches_by_group.setdefault(letter, []).append(match_number)
        self._cache: dict[int, set[str]] = {}

    def parse_fixed_matchup(self, matchup_text: str) -> tuple[str, str] | None:
        """Split a literal matchup string into left and right participants."""
        return parse_fixed_matchup_text(matchup_text)

    def expand_groups(self, group_token: str) -> list[str]:
        """Expand slash-separated group tokens such as `A/B/C` into letters."""
        return expand_group_token(group_token)

    def teams_from_groups(self, groups: Iterable[str]) -> set[str]:
        """Return all teams that belong to the supplied group letters."""
        teams: set[str] = set()
        for group_letter in groups:
            teams.update(self.group_to_teams.get(group_letter, []))
        return teams

    def parse_group_slot(self, slot_text: str) -> tuple[set[str], str] | None:
        """Parse slots such as `Group E winners` or `Group A/B third place`."""
        parsed = parse_group_slot_text(slot_text)
        if parsed is None:
            return None
        groups, slot_type = parsed
        return set(groups), slot_type

    def _has_played_score(self, result: MatchResultState | None) -> bool:
        if result is None or not result.played:
            return False
        if result.home_team is None or result.away_team is None:
            return False
        if result.home_goals is None or result.away_goals is None:
            return False
        return True

    def _group_slot_resolved_teams(
        self,
        groups: Iterable[str],
        slot_type: str,
        match_results: dict[int, MatchResultState],
    ) -> set[str]:
        standings = self.compute_group_standings(match_results=match_results)
        complete_groups = self.completed_groups(match_results=match_results)
        resolved: set[str] = set()

        slot_index_by_type = {
            "winners": 0,
            "runners-up": 1,
            "third place": 2,
        }
        idx = slot_index_by_type.get(slot_type)
        if idx is None:
            return resolved

        for group in groups:
            if group not in complete_groups:
                continue
            rows = standings.get(group, [])
            if len(rows) > idx:
                resolved.add(rows[idx].team)
        return resolved

    def _match_side_team(
        self,
        match_number: int,
        outcome: str,
        match_results: dict[int, MatchResultState],
    ) -> str | None:
        result = match_results.get(match_number)
        if not self._has_played_score(result):
            return None
        assert result is not None
        if result.home_goals == result.away_goals:
            return None
        home_won = result.home_goals > result.away_goals
        if outcome == "winner":
            return result.home_team if home_won else result.away_team
        if outcome == "loser":
            return result.away_team if home_won else result.home_team
        return None

    def resolve_slot_teams(
        self,
        slot_text: str,
        stack: set[int] | None = None,
        match_results: dict[int, MatchResultState] | None = None,
    ) -> set[str]:
        """Resolve the possible teams that can occupy a bracket slot."""
        slot_text = slot_text.strip()
        stack = stack or set()
        match_results = match_results or {}

        group_slot = self.parse_group_slot(slot_text)
        if group_slot:
            groups, slot_type = group_slot
            resolved = self._group_slot_resolved_teams(
                groups=groups,
                slot_type=slot_type,
                match_results=match_results,
            )
            unresolved_groups = set(groups) - self.completed_groups(match_results)
            fallback = self.teams_from_groups(unresolved_groups)
            return resolved | fallback

        winner_match = parse_match_reference(slot_text, outcome="winner")
        if winner_match is not None:
            return self.resolve_match_team_pool(
                winner_match,
                stack,
                match_results=match_results,
                outcome="winner",
            )

        loser_match = parse_match_reference(slot_text, outcome="loser")
        if loser_match is not None:
            return self.resolve_match_team_pool(
                loser_match,
                stack,
                match_results=match_results,
                outcome="loser",
            )

        return set()

    def resolve_match_team_pool(
        self,
        match_number: int,
        stack: set[int] | None = None,
        match_results: dict[int, MatchResultState] | None = None,
        outcome: str | None = None,
    ) -> set[str]:
        """Resolve the possible teams involved in a scheduled match."""
        stack = stack or set()
        match_results = match_results or {}
        if outcome:
            resolved_team = self._match_side_team(match_number, outcome, match_results)
            if resolved_team is not None:
                return {resolved_team}

        if not match_results and match_number in self._cache and outcome is None:
            return self._cache[match_number]
        if match_number in stack:
            return set()

        match = self.schedule_by_number.get(match_number)
        if not match:
            return set()

        stack.add(match_number)
        matchup_text = str(match.get("comment") or match.get("matchup") or "")
        fixed = self.parse_fixed_matchup(matchup_text)
        if not fixed:
            if not match_results and outcome is None:
                self._cache[match_number] = set()
            stack.remove(match_number)
            return set()

        left_slot, right_slot = fixed
        left_teams = self.resolve_slot_teams(left_slot, stack, match_results=match_results)
        right_teams = self.resolve_slot_teams(right_slot, stack, match_results=match_results)

        if not left_teams and " vs " in matchup_text:
            left_teams = {left_slot}
        if not right_teams and " vs " in matchup_text:
            right_teams = {right_slot}

        resolved_pool = left_teams | right_teams
        if not match_results and outcome is None:
            self._cache[match_number] = resolved_pool
        stack.remove(match_number)
        return resolved_pool

    def compute_group_standings(
        self,
        match_results: dict[int, MatchResultState],
    ) -> dict[str, list[GroupStandingRow]]:
        """Compute current group standings from the supplied played results."""
        tables: dict[str, dict[str, GroupStandingRow]] = {
            group: {team: GroupStandingRow(team=team) for team in teams}
            for group, teams in self.group_to_teams.items()
        }
        for group, match_numbers in self.group_stage_matches_by_group.items():
            table = tables.get(group, {})
            for match_number in match_numbers:
                result = match_results.get(match_number)
                if not self._has_played_score(result):
                    continue
                assert result is not None
                home = table.get(result.home_team)
                away = table.get(result.away_team)
                if home is None or away is None:
                    continue
                home.goals_for += result.home_goals
                home.goals_against += result.away_goals
                away.goals_for += result.away_goals
                away.goals_against += result.home_goals

                if result.home_goals > result.away_goals:
                    home.points += 3
                elif result.home_goals < result.away_goals:
                    away.points += 3
                else:
                    home.points += 1
                    away.points += 1

        return {
            group: sorted(
                rows.values(),
                key=lambda row: (
                    -row.points,
                    -row.goal_difference,
                    -row.goals_for,
                    row.team,
                ),
            )
            for group, rows in tables.items()
        }

    def completed_groups(self, match_results: dict[int, MatchResultState]) -> set[str]:
        """Return the groups whose full schedules have confirmed results."""
        complete: set[str] = set()
        for group, match_numbers in self.group_stage_matches_by_group.items():
            if not match_numbers:
                continue
            if all(self._has_played_score(match_results.get(match_number)) for match_number in match_numbers):
                complete.add(group)
        return complete

    def build_rule_based_pairs(
        self,
        match_number: int,
        match_results: dict[int, MatchResultState] | None = None,
    ) -> list[tuple[str, str]]:
        """Return all valid ordered home/away pairs allowed for a match."""
        match_results = match_results or {}
        selected_match = next(
            (match for match in self.schedule if str(match.get("match_number")) == str(match_number)),
            None,
        )
        if not selected_match:
            return []

        matchup_text = str(selected_match.get("comment") or selected_match.get("matchup") or "")
        slots = self.parse_fixed_matchup(matchup_text)
        if not slots:
            return []
        left_slot, right_slot = slots

        left_teams = self.resolve_slot_teams(left_slot, match_results=match_results)
        right_teams = self.resolve_slot_teams(right_slot, match_results=match_results)
        if not left_teams or not right_teams:
            return []

        pairs: list[tuple[str, str]] = []
        for home_team in sorted(left_teams):
            for away_team in sorted(right_teams):
                if home_team != away_team:
                    pairs.append((home_team, away_team))
        return pairs
