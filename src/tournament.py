from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass
class TournamentStructureResolver:
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
        self._cache: dict[int, set[str]] = {}

    def parse_fixed_matchup(self, matchup_text: str) -> tuple[str, str] | None:
        parts = re.split(r"\s+vs\s+", str(matchup_text), maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2:
            return None
        return parts[0].strip(), parts[1].strip()

    def expand_groups(self, group_token: str) -> list[str]:
        return [group.strip() for group in group_token.split("/") if group.strip()]

    def teams_from_groups(self, groups: Iterable[str]) -> set[str]:
        teams: set[str] = set()
        for group_letter in groups:
            teams.update(self.group_to_teams.get(group_letter, []))
        return teams

    def parse_group_slot(self, slot_text: str) -> tuple[set[str], str] | None:
        pattern = r"^Group\s+([A-Z](?:/[A-Z])*)\s+(winners|runners-up|third place)$"
        match = re.match(pattern, slot_text.strip(), flags=re.IGNORECASE)
        if not match:
            return None
        groups = self.expand_groups(match.group(1).upper())
        slot_type = match.group(2).lower()
        return set(groups), slot_type

    def resolve_slot_teams(self, slot_text: str, stack: set[int] | None = None) -> set[str]:
        slot_text = slot_text.strip()
        stack = stack or set()

        group_slot = self.parse_group_slot(slot_text)
        if group_slot:
            groups, _slot_type = group_slot
            return self.teams_from_groups(groups)

        winner_match = re.match(r"^Winner Match\s+(\d+)$", slot_text, flags=re.IGNORECASE)
        if winner_match:
            return self.resolve_match_team_pool(int(winner_match.group(1)), stack)

        loser_match = re.match(r"^Loser Match\s+(\d+)$", slot_text, flags=re.IGNORECASE)
        if loser_match:
            return self.resolve_match_team_pool(int(loser_match.group(1)), stack)

        return set()

    def resolve_match_team_pool(self, match_number: int, stack: set[int] | None = None) -> set[str]:
        stack = stack or set()
        if match_number in self._cache:
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
            self._cache[match_number] = set()
            stack.remove(match_number)
            return self._cache[match_number]

        left_slot, right_slot = fixed
        left_teams = self.resolve_slot_teams(left_slot, stack)
        right_teams = self.resolve_slot_teams(right_slot, stack)

        if not left_teams and " vs " in matchup_text:
            left_teams = {left_slot}
        if not right_teams and " vs " in matchup_text:
            right_teams = {right_slot}

        self._cache[match_number] = left_teams | right_teams
        stack.remove(match_number)
        return self._cache[match_number]

    def build_rule_based_pairs(self, match_number: int) -> list[tuple[str, str]]:
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

        left_teams = self.resolve_slot_teams(left_slot)
        right_teams = self.resolve_slot_teams(right_slot)
        if not left_teams or not right_teams:
            return []

        pairs: list[tuple[str, str]] = []
        for home_team in sorted(left_teams):
            for away_team in sorted(right_teams):
                if home_team != away_team:
                    pairs.append((home_team, away_team))
        return pairs
