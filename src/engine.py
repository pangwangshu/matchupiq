from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Iterable

try:
    from src.models import MatchupCandidate, PredictionResponse
    from src.signals import EloStrengthSignal, GroupFormSignal, SignalContext, TravelRestSignal
except ModuleNotFoundError:
    from models import MatchupCandidate, PredictionResponse
    from signals import EloStrengthSignal, GroupFormSignal, SignalContext, TravelRestSignal

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "matches_2026.json"
WORLD_CUP_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "worldcup_2026_static.json"


class MatchupPredictor:
    def __init__(self) -> None:
        self.group_form = GroupFormSignal()
        self.elo = EloStrengthSignal()
        self.travel_rest = TravelRestSignal()

    def _load_matches(self) -> dict:
        with DATA_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _load_participant_teams(self) -> list[str]:
        with WORLD_CUP_DATA_PATH.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        participants = payload.get("participants", [])
        return [p["name"] for p in participants if p.get("name")]

    def _load_world_cup_data(self) -> dict:
        with WORLD_CUP_DATA_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _parse_fixed_matchup(self, matchup_text: str) -> tuple[str, str] | None:
        parts = re.split(r"\s+vs\s+", str(matchup_text), maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2:
            return None
        return parts[0].strip(), parts[1].strip()

    def _expand_groups(self, group_token: str) -> list[str]:
        return [g.strip() for g in group_token.split("/") if g.strip()]

    def _teams_from_groups(
        self, groups: Iterable[str], group_to_teams: dict[str, list[str]]
    ) -> set[str]:
        teams: set[str] = set()
        for group_letter in groups:
            teams.update(group_to_teams.get(group_letter, []))
        return teams

    def _parse_group_slot(self, slot_text: str) -> tuple[set[str], str] | None:
        pattern = r"^Group\s+([A-Z](?:/[A-Z])*)\s+(winners|runners-up|third place)$"
        match = re.match(pattern, slot_text.strip(), flags=re.IGNORECASE)
        if not match:
            return None
        groups = self._expand_groups(match.group(1).upper())
        slot_type = match.group(2).lower()
        return set(groups), slot_type

    def _resolve_slot_teams(
        self,
        slot_text: str,
        schedule_by_number: dict[int, dict],
        group_to_teams: dict[str, list[str]],
        cache: dict[int, set[str]],
        stack: set[int],
    ) -> set[str]:
        slot_text = slot_text.strip()

        group_slot = self._parse_group_slot(slot_text)
        if group_slot:
            groups, _slot_type = group_slot
            return self._teams_from_groups(groups, group_to_teams)

        winner_match = re.match(r"^Winner Match\s+(\d+)$", slot_text, flags=re.IGNORECASE)
        if winner_match:
            return self._resolve_match_team_pool(
                int(winner_match.group(1)), schedule_by_number, group_to_teams, cache, stack
            )

        loser_match = re.match(r"^Loser Match\s+(\d+)$", slot_text, flags=re.IGNORECASE)
        if loser_match:
            return self._resolve_match_team_pool(
                int(loser_match.group(1)), schedule_by_number, group_to_teams, cache, stack
            )

        return set()

    def _resolve_match_team_pool(
        self,
        match_number: int,
        schedule_by_number: dict[int, dict],
        group_to_teams: dict[str, list[str]],
        cache: dict[int, set[str]],
        stack: set[int],
    ) -> set[str]:
        if match_number in cache:
            return cache[match_number]
        if match_number in stack:
            return set()

        match = schedule_by_number.get(match_number)
        if not match:
            return set()

        stack.add(match_number)
        matchup_text = str(match.get("comment") or match.get("matchup") or "")
        fixed = self._parse_fixed_matchup(matchup_text)
        if not fixed:
            cache[match_number] = set()
            stack.remove(match_number)
            return cache[match_number]

        left_slot, right_slot = fixed
        left_teams = self._resolve_slot_teams(
            left_slot, schedule_by_number, group_to_teams, cache, stack
        )
        right_teams = self._resolve_slot_teams(
            right_slot, schedule_by_number, group_to_teams, cache, stack
        )

        if not left_teams and " vs " in matchup_text:
            left_teams = {left_slot}
        if not right_teams and " vs " in matchup_text:
            right_teams = {right_slot}

        cache[match_number] = left_teams | right_teams
        stack.remove(match_number)
        return cache[match_number]

    def _build_rule_based_pairs(self, match_number: int) -> list[tuple[str, str]]:
        world_cup = self._load_world_cup_data()
        schedule = world_cup.get("schedule", [])
        groups = world_cup.get("groups", [])
        selected_match = next(
            (m for m in schedule if str(m.get("match_number")) == str(match_number)),
            None,
        )
        if not selected_match:
            return []

        matchup_text = str(selected_match.get("comment") or selected_match.get("matchup") or "")
        slots = self._parse_fixed_matchup(matchup_text)
        if not slots:
            return []
        left_slot, right_slot = slots

        group_to_teams: dict[str, list[str]] = {}
        for g in groups:
            group_name = str(g.get("group", ""))
            letter = group_name.replace("Group", "").strip().upper()
            if letter:
                group_to_teams[letter] = [str(t) for t in g.get("countries", [])]

        schedule_by_number = {
            int(m["match_number"]): m
            for m in schedule
            if isinstance(m.get("match_number"), int)
        }
        cache: dict[int, set[str]] = {}
        left_teams = self._resolve_slot_teams(left_slot, schedule_by_number, group_to_teams, cache, set())
        right_teams = self._resolve_slot_teams(right_slot, schedule_by_number, group_to_teams, cache, set())
        if not left_teams or not right_teams:
            return []

        pairs: list[tuple[str, str]] = []
        for home_team in sorted(left_teams):
            for away_team in sorted(right_teams):
                if home_team != away_team:
                    pairs.append((home_team, away_team))
        return pairs

    def _build_baseline_candidates(self, match_id: str, limit: int = 10) -> list[MatchupCandidate]:
        match_number: int | None = None
        try:
            match_number = int(match_id)
        except (TypeError, ValueError):
            match_number = None

        rule_based_pairs = self._build_rule_based_pairs(match_number=match_number) if match_number is not None else []
        if rule_based_pairs:
            all_pairs = rule_based_pairs[:]
        else:
            teams = self._load_participant_teams()
            if len(teams) < 2:
                raise ValueError("Not enough participant teams to build baseline predictions.")
            all_pairs = []
            for i, home_team in enumerate(teams):
                for j, away_team in enumerate(teams):
                    if i != j:
                        all_pairs.append((home_team, away_team))

        random.shuffle(all_pairs)
        candidates: list[MatchupCandidate] = []
        used_unordered_pairs: set[tuple[str, str]] = set()
        score = 1.0

        for home_team, away_team in all_pairs:
            pair_key = tuple(sorted((home_team, away_team)))
            if pair_key in used_unordered_pairs:
                continue
            used_unordered_pairs.add(pair_key)
            candidates.append(
                MatchupCandidate(
                    home_team=home_team,
                    away_team=away_team,
                    score=round(score, 4),
                    reason=(
                        "Rule-constrained baseline candidate."
                        if rule_based_pairs
                        else "Baseline UI test: matchup can be any two different participant teams."
                    ),
                )
            )
            score = max(0.0, score - 0.01)
            if len(candidates) >= limit:
                break

        return candidates

    def predict(self, match_id: str) -> PredictionResponse:
        # Baseline mode: for UI testing, ignore tournament path rules and signal scoring.
        ranked = self._build_baseline_candidates(match_id=match_id, limit=10)
        return PredictionResponse(
            match_id=match_id,
            status="predicted",
            top_candidates=ranked,
        )
