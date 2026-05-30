from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

try:
    from src.models import MatchupCandidate, PredictionResponse
    from src.signals import EloStrengthSignal, GroupFormSignal, SignalContext, TravelRestSignal
    from src.tournament import MatchResultState, TournamentStructureResolver
    from src.world_ranking import (
        FifaRankingStrengthProvider,
        StrengthProvider,
        WorldRankingTournamentSimulator,
    )
except ModuleNotFoundError:
    from models import MatchupCandidate, PredictionResponse
    from signals import EloStrengthSignal, GroupFormSignal, SignalContext, TravelRestSignal
    from tournament import MatchResultState, TournamentStructureResolver
    from world_ranking import (
        FifaRankingStrengthProvider,
        StrengthProvider,
        WorldRankingTournamentSimulator,
    )

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "matches_2026.json"
WORLD_CUP_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "worldcup_2026_static.json"
FIFA_RANKING_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "fifa_men_ranking_static.json"


class MatchDataProvider(Protocol):
    def load_matches(self) -> dict:
        ...

    def load_world_cup_data(self) -> dict:
        ...

    def load_fifa_ranking_data(self) -> dict:
        ...

    def load_participant_teams(self) -> list[str]:
        ...


class TournamentSimulator(Protocol):
    def predict_matchup_candidates(self, match_number: int, limit: int = 10) -> list[tuple[str, str, float]]:
        ...


class TournamentSimulatorFactory(Protocol):
    def create(
        self,
        world_cup_data: dict,
        fifa_ranking_data: dict,
        match_results: dict[int, MatchResultState] | None = None,
    ) -> TournamentSimulator:
        ...


@dataclass
class StaticJsonMatchDataProvider:
    matches_path: Path = DATA_PATH
    world_cup_path: Path = WORLD_CUP_DATA_PATH
    fifa_ranking_path: Path = FIFA_RANKING_DATA_PATH

    def load_matches(self) -> dict:
        with self.matches_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def load_world_cup_data(self) -> dict:
        with self.world_cup_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def load_fifa_ranking_data(self) -> dict:
        with self.fifa_ranking_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def load_participant_teams(self) -> list[str]:
        payload = self.load_world_cup_data()
        participants = payload.get("participants", [])
        return [p["name"] for p in participants if p.get("name")]


class WorldRankingSimulatorFactory:
    def create_strength_provider(self, fifa_ranking_data: dict) -> StrengthProvider:
        return FifaRankingStrengthProvider(
            fifa_ranking_data=fifa_ranking_data,
            default_rank_for_unlisted_team=120,
            default_points_fallback=1400.0,
        )

    def create(
        self,
        world_cup_data: dict,
        fifa_ranking_data: dict,
        match_results: dict[int, MatchResultState] | None = None,
    ) -> WorldRankingTournamentSimulator:
        strength_provider = self.create_strength_provider(fifa_ranking_data)
        return WorldRankingTournamentSimulator(
            world_cup_data=world_cup_data,
            fifa_ranking_data=fifa_ranking_data,
            strength_provider=strength_provider,
            match_results=match_results,
        )


class MatchupPredictor:
    def __init__(
        self,
        data_provider: MatchDataProvider | None = None,
        simulator_factory: TournamentSimulatorFactory | None = None,
    ) -> None:
        self.data_provider = data_provider or StaticJsonMatchDataProvider()
        self.simulator_factory = simulator_factory or WorldRankingSimulatorFactory()
        self.group_form = GroupFormSignal()
        self.elo = EloStrengthSignal()
        self.travel_rest = TravelRestSignal()

    def _load_matches(self) -> dict:
        return self.data_provider.load_matches()

    def _load_participant_teams(self) -> list[str]:
        return self.data_provider.load_participant_teams()

    def _load_world_cup_data(self) -> dict:
        return self.data_provider.load_world_cup_data()

    def _load_fifa_ranking_data(self) -> dict:
        return self.data_provider.load_fifa_ranking_data()

    def _to_int_or_none(self, raw: object) -> int | None:
        if raw is None:
            return None
        try:
            return int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    def _extract_match_number(self, item: dict) -> int | None:
        direct = self._to_int_or_none(item.get("match_number"))
        if direct is not None:
            return direct
        label = str(item.get("label", ""))
        match = re.search(r"\bMatch\s+(\d+)\b", label, flags=re.IGNORECASE)
        if not match:
            return None
        return self._to_int_or_none(match.group(1))

    def _load_match_results_state(self) -> dict[int, MatchResultState]:
        payload = self._load_matches()
        if not isinstance(payload, dict):
            return {}

        played_statuses = {"played", "completed", "complete", "final", "finished", "ft"}
        out: dict[int, MatchResultState] = {}
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            match_number = self._extract_match_number(item)
            if match_number is None:
                continue

            status = str(item.get("status", "")).strip().lower()
            played = bool(item.get("played")) or status in played_statuses
            home_team = item.get("confirmed_home") or item.get("home_team") or item.get("home")
            away_team = item.get("confirmed_away") or item.get("away_team") or item.get("away")
            home_goals = (
                self._to_int_or_none(item.get("home_goals"))
                if item.get("home_goals") is not None
                else self._to_int_or_none(item.get("home_score"))
            )
            away_goals = (
                self._to_int_or_none(item.get("away_goals"))
                if item.get("away_goals") is not None
                else self._to_int_or_none(item.get("away_score"))
            )
            out[match_number] = MatchResultState(
                played=played,
                home_team=str(home_team) if home_team is not None else None,
                away_team=str(away_team) if away_team is not None else None,
                home_goals=home_goals,
                away_goals=away_goals,
            )
        return out

    def _resolver(self) -> TournamentStructureResolver:
        world_cup = self._load_world_cup_data()
        return TournamentStructureResolver(
            groups=world_cup.get("groups", []),
            schedule=world_cup.get("schedule", []),
        )

    def _parse_fixed_matchup(self, matchup_text: str) -> tuple[str, str] | None:
        return self._resolver().parse_fixed_matchup(matchup_text)

    def _expand_groups(self, group_token: str) -> list[str]:
        return self._resolver().expand_groups(group_token)

    def _teams_from_groups(self, groups: list[str], group_to_teams: dict[str, list[str]]) -> set[str]:
        resolver = self._resolver()
        resolver.group_to_teams = group_to_teams
        return resolver.teams_from_groups(groups)

    def _parse_group_slot(self, slot_text: str) -> tuple[set[str], str] | None:
        return self._resolver().parse_group_slot(slot_text)

    def _resolve_slot_teams(
        self,
        slot_text: str,
        schedule_by_number: dict[int, dict],
        group_to_teams: dict[str, list[str]],
        cache: dict[int, set[str]],
        stack: set[int],
    ) -> set[str]:
        resolver = self._resolver()
        resolver.schedule_by_number = schedule_by_number
        resolver.group_to_teams = group_to_teams
        resolver._cache = cache
        return resolver.resolve_slot_teams(slot_text, stack)

    def _resolve_match_team_pool(
        self,
        match_number: int,
        schedule_by_number: dict[int, dict],
        group_to_teams: dict[str, list[str]],
        cache: dict[int, set[str]],
        stack: set[int],
    ) -> set[str]:
        resolver = self._resolver()
        resolver.schedule_by_number = schedule_by_number
        resolver.group_to_teams = group_to_teams
        resolver._cache = cache
        return resolver.resolve_match_team_pool(match_number, stack)

    def _build_rule_based_pairs(self, match_number: int) -> list[tuple[str, str]]:
        return self._resolver().build_rule_based_pairs(
            match_number,
            match_results=self._load_match_results_state(),
        )

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
            pair_key = tuple(sorted((home_team, away_team)))  # type: ignore[assignment]
            if pair_key in used_unordered_pairs:
                continue
            used_unordered_pairs.add(pair_key)  # type: ignore[arg-type]
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

    def _build_world_ranking_candidates(self, match_id: str, limit: int = 10) -> list[MatchupCandidate] | None:
        try:
            match_number = int(match_id)
        except (TypeError, ValueError):
            return None

        world_cup_data = self._load_world_cup_data()
        fifa_ranking_data = self._load_fifa_ranking_data()
        simulator = self.simulator_factory.create(
            world_cup_data=world_cup_data,
            fifa_ranking_data=fifa_ranking_data,
            match_results=self._load_match_results_state(),
        )
        predicted = simulator.predict_matchup_candidates(
            match_number=match_number,
            limit=limit,
        )
        if not predicted:
            return None

        out: list[MatchupCandidate] = []
        for home_team, away_team, probability in predicted:
            out.append(
                MatchupCandidate(
                    home_team=home_team,
                    away_team=away_team,
                    score=round(probability, 4),
                    reason=(
                        "Predicted via FIFA world-ranking scenario search "
                        "(group standings + knockout outcome branching)."
                    ),
                )
            )
        return out

    def predict(self, match_id: str) -> PredictionResponse:
        world_ranking_candidates = None
        try:
            world_ranking_candidates = self._build_world_ranking_candidates(match_id=match_id, limit=10)
        except Exception:
            world_ranking_candidates = None

        if world_ranking_candidates is not None:
            ranked = world_ranking_candidates
        else:
            ranked = self._build_baseline_candidates(match_id=match_id, limit=10)
        return PredictionResponse(
            match_id=match_id,
            status="predicted",
            top_candidates=ranked,
        )
