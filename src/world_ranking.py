from __future__ import annotations

import json
import re
from dataclasses import dataclass
from math import exp, isfinite
from pathlib import Path
from typing import Any, Iterable, Protocol

try:
    from src.tournament import MatchResultState, TournamentStructureResolver
except ModuleNotFoundError:
    from tournament import MatchResultState, TournamentStructureResolver

DEFAULT_MODEL_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "world_ranking_model_config.json"


@dataclass
class WorldRankingModelConfig:
    draw_band: float = 20.0
    decisive_band: float = 80.0

    group_draw_probability_min: float = 0.10
    group_draw_probability_max: float = 0.32
    group_draw_probability_base: float = 0.30
    group_draw_diff_divisor: float = 700.0
    group_win_sigmoid_divisor: float = 85.0

    knockout_win_sigmoid_divisor: float = 75.0
    knockout_rank_bias_divisor: float = 400.0
    knockout_win_probability_min: float = 0.05
    knockout_win_probability_max: float = 0.95

    slot_contender_min_weight: float = 1.0

    default_rank_for_unlisted_team: int = 120
    default_points_fallback: float = 1400.0

    prediction_group_beam_width: int = 64
    prediction_group_max_scenarios: int = 20
    prediction_world_beam_width: int = 256
    prediction_min_branch_probability: float = 1e-5

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WorldRankingModelConfig:
        config = cls()
        for field_name in cls.__dataclass_fields__.keys():
            if field_name in raw:
                setattr(config, field_name, raw[field_name])
        return config

    @classmethod
    def load(cls, path: Path) -> WorldRankingModelConfig:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Model config must be a JSON object: {path}")
        return cls.from_dict(payload)


@dataclass(frozen=True)
class TeamStrengthProfile:
    team: str
    rating: float
    seed_rank: int


@dataclass(frozen=True)
class MatchContext:
    match_number: int | None
    stage: str | None
    date: str | None
    group: str | None


class TeamPowerModel(Protocol):
    def team_rating(self, team: str) -> float:
        ...

    def team_rank(self, team: str) -> int:
        ...


class PairwiseWinModel(Protocol):
    """Contract for match-level win probability models.

    Implementations may be rating-based, market-based, or hybrid.
    For market-backed implementations, unavailable/stale/unreliable market data
    should be handled via deterministic fallback (typically delegating to a
    rating-based model) rather than ad-hoc random behavior.
    """

    def group_outcomes(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model: TeamPowerModel,
        model_config: WorldRankingModelConfig,
        decisive_band: float,
    ) -> list["MatchOutcome"]:
        # Contract:
        # - probabilities must be finite and non-negative
        # - probabilities must sum to 1.0 (within floating-point tolerance)
        # - if market data is unavailable/stale/unreliable, use deterministic
        #   fallback logic (e.g. delegate to rating-based model)
        ...

    def knockout_home_win_probability(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model: TeamPowerModel,
        model_config: WorldRankingModelConfig,
        draw_band: float,
    ) -> float:
        # Contract:
        # - return finite probability in [0.0, 1.0]
        # - if market data is unavailable/stale/unreliable, use deterministic
        #   fallback logic (e.g. delegate to rating-based model)
        # - simulator will clamp boundary values as a final safety guard
        ...


class FifaTeamPowerModel:
    def __init__(
        self,
        fifa_ranking_data: dict,
        *,
        default_rank_for_unlisted_team: int,
        default_points_fallback: float,
    ) -> None:
        self.profiles_by_team = self._build_strength_profile_map(fifa_ranking_data)
        all_points = sorted([entry.rating for entry in self.profiles_by_team.values()])
        self.default_points = (
            all_points[len(all_points) // 2]
            if all_points
            else default_points_fallback
        )
        self.default_rank = default_rank_for_unlisted_team

    def _build_strength_profile_map(self, fifa_ranking_data: dict) -> dict[str, TeamStrengthProfile]:
        out: dict[str, TeamStrengthProfile] = {}
        for row in fifa_ranking_data.get("participants_rankings", []):
            team = str(row.get("participant_name", "")).strip()
            if not team:
                continue
            out[team] = TeamStrengthProfile(
                team=team,
                rating=float(row.get("points", 0.0)),
                seed_rank=int(row.get("rank", 999)),
            )
        return out

    def _team_profile(self, team: str) -> TeamStrengthProfile:
        return self.profiles_by_team.get(
            team,
            TeamStrengthProfile(team=team, rating=self.default_points, seed_rank=self.default_rank),
        )

    def team_rating(self, team: str) -> float:
        return self._team_profile(team).rating

    def team_rank(self, team: str) -> int:
        return self._team_profile(team).seed_rank


class RatingPairwiseWinModel:
    @staticmethod
    def _sigmoid(value: float) -> float:
        if value >= 0:
            z = exp(-value)
            return 1.0 / (1.0 + z)
        z = exp(value)
        return z / (1.0 + z)

    def _pairwise_strength_diff(
        self,
        team_a: str,
        team_b: str,
        team_power_model: TeamPowerModel,
    ) -> float:
        return team_power_model.team_rating(team_a) - team_power_model.team_rating(team_b)

    def _pairwise_win_probability(
        self,
        team_a: str,
        team_b: str,
        *,
        team_power_model: TeamPowerModel,
        sigmoid_divisor: float,
    ) -> float:
        diff = self._pairwise_strength_diff(team_a, team_b, team_power_model)
        return self._sigmoid(diff / sigmoid_divisor)

    def group_outcomes(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model: TeamPowerModel,
        model_config: WorldRankingModelConfig,
        decisive_band: float,
    ) -> list["MatchOutcome"]:
        _ = match_context
        diff = self._pairwise_strength_diff(home_team, away_team, team_power_model)
        abs_diff = abs(diff)

        draw_probability = max(
            model_config.group_draw_probability_min,
            min(
                model_config.group_draw_probability_max,
                model_config.group_draw_probability_base
                - (abs_diff / model_config.group_draw_diff_divisor),
            ),
        )
        home_given_no_draw = self._pairwise_win_probability(
            home_team,
            away_team,
            team_power_model=team_power_model,
            sigmoid_divisor=model_config.group_win_sigmoid_divisor,
        )
        home_win_probability = (1.0 - draw_probability) * home_given_no_draw
        away_win_probability = max(0.0, 1.0 - draw_probability - home_win_probability)

        margin = 2 if abs_diff >= decisive_band else 1
        return [
            MatchOutcome(home_goals=1 + margin, away_goals=1, probability=home_win_probability),
            MatchOutcome(home_goals=1, away_goals=1, probability=draw_probability),
            MatchOutcome(home_goals=1, away_goals=1 + margin, probability=away_win_probability),
        ]

    def knockout_home_win_probability(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model: TeamPowerModel,
        model_config: WorldRankingModelConfig,
        draw_band: float,
    ) -> float:
        _ = match_context
        diff = self._pairwise_strength_diff(home_team, away_team, team_power_model)
        base = self._pairwise_win_probability(
            home_team,
            away_team,
            team_power_model=team_power_model,
            sigmoid_divisor=model_config.knockout_win_sigmoid_divisor,
        )
        if abs(diff) <= draw_band:
            rank_home = team_power_model.team_rank(home_team)
            rank_away = team_power_model.team_rank(away_team)
            rank_bias = (rank_away - rank_home) / model_config.knockout_rank_bias_divisor
            base = 0.5 + rank_bias
        return max(
            model_config.knockout_win_probability_min,
            min(model_config.knockout_win_probability_max, base),
        )


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


@dataclass(frozen=True)
class MatchOutcome:
    home_goals: int
    away_goals: int
    probability: float


@dataclass
class GroupScenario:
    group: str
    probability: float
    standings: list[TeamStanding]
    signature: tuple


@dataclass
class WorldScenario:
    probability: float
    group_scenarios: dict[str, GroupScenario]
    signature: tuple


class WorldRankingTournamentSimulator:
    def __init__(
        self,
        world_cup_data: dict,
        team_power_model: TeamPowerModel,
        pairwise_win_model: PairwiseWinModel | None = None,
        match_results: dict[int, MatchResultState] | None = None,
        draw_band: float | None = None,
        decisive_band: float | None = None,
        model_config: WorldRankingModelConfig | None = None,
        model_config_path: Path | None = DEFAULT_MODEL_CONFIG_PATH,
    ) -> None:
        self.world_cup_data = world_cup_data
        self.model_config = self._load_model_config(model_config, model_config_path)
        if draw_band is not None:
            self.model_config.draw_band = draw_band
        if decisive_band is not None:
            self.model_config.decisive_band = decisive_band
        self.draw_band = self.model_config.draw_band
        self.decisive_band = self.model_config.decisive_band
        self.resolver = TournamentStructureResolver(
            groups=world_cup_data.get("groups", []),
            schedule=world_cup_data.get("schedule", []),
        )
        self.team_power_model = team_power_model
        self.pairwise_win_model = pairwise_win_model or RatingPairwiseWinModel()
        self.match_results = match_results or {}
        self.group_matches = self._build_group_matches()
        self.match_by_number = {
            int(match["match_number"]): match
            for match in self.world_cup_data.get("schedule", [])
            if isinstance(match.get("match_number"), int)
        }

    def _load_model_config(
        self,
        model_config: WorldRankingModelConfig | None,
        model_config_path: Path | None,
    ) -> WorldRankingModelConfig:
        if model_config is not None:
            return model_config
        if model_config_path is not None and model_config_path.exists():
            try:
                return WorldRankingModelConfig.load(model_config_path)
            except Exception:
                return WorldRankingModelConfig()
        return WorldRankingModelConfig()

    def _build_group_matches(self) -> dict[str, list[tuple[int, str, str]]]:
        out: dict[str, list[tuple[int, str, str]]] = {}
        group_stage = sorted(
            [
                match
                for match in self.world_cup_data.get("schedule", [])
                if match.get("stage") == "group_stage"
            ],
            key=lambda match: int(match.get("match_number", 0)),
        )
        for match in group_stage:
            group_name = str(match.get("group", ""))
            group = group_name.replace("Group", "").strip().upper()
            if not group:
                continue
            slots = self.resolver.parse_fixed_matchup(str(match.get("matchup") or match.get("comment") or ""))
            if not slots:
                continue
            match_number = match.get("match_number")
            if not isinstance(match_number, int):
                continue
            out.setdefault(group, []).append((match_number, slots[0], slots[1]))
        return out

    def _played_result(self, match_number: int) -> MatchResultState | None:
        result = self.match_results.get(match_number)
        if result is None or not result.played:
            return None
        if result.home_team is None or result.away_team is None:
            return None
        if result.home_goals is None or result.away_goals is None:
            return None
        return result

    def _team_profile(self, team: str) -> TeamStrengthProfile:
        return TeamStrengthProfile(
            team=team,
            rating=self.team_power_model.team_rating(team),
            seed_rank=self.team_power_model.team_rank(team),
        )

    def _match_context(self, match_number: int | None, fallback_group: str | None = None) -> MatchContext:
        if match_number is None:
            return MatchContext(
                match_number=None,
                stage=None,
                date=None,
                group=fallback_group,
            )
        match = self.match_by_number.get(match_number, {})
        stage = match.get("stage")
        date = match.get("date")
        group = match.get("group") or fallback_group
        return MatchContext(
            match_number=match_number,
            stage=str(stage) if stage is not None else None,
            date=str(date) if date is not None else None,
            group=str(group) if group is not None else None,
        )

    def _validate_group_outcomes(
        self,
        outcomes: list[MatchOutcome],
        match_context: MatchContext,
    ) -> list[MatchOutcome]:
        if not outcomes:
            raise ValueError(f"PairwiseWinModel returned no group outcomes for match {match_context.match_number}.")

        total = 0.0
        for outcome in outcomes:
            if not isfinite(outcome.probability):
                raise ValueError(
                    f"PairwiseWinModel returned non-finite group probability for match {match_context.match_number}."
                )
            if outcome.probability < 0.0:
                raise ValueError(
                    f"PairwiseWinModel returned negative group probability for match {match_context.match_number}."
                )
            total += outcome.probability

        # Tight tolerance keeps contracts explicit and deterministic for providers.
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"PairwiseWinModel group probabilities must sum to 1.0; got {total:.10f} "
                f"for match {match_context.match_number}."
            )
        return outcomes

    def _clamp_knockout_probability(self, probability: float, match_context: MatchContext) -> float:
        if not isfinite(probability):
            raise ValueError(
                f"PairwiseWinModel returned non-finite knockout probability for match {match_context.match_number}."
            )
        return max(0.0, min(1.0, probability))

    def _group_outcomes(
        self,
        home_team: str,
        away_team: str,
        match_number: int | None,
        group: str | None = None,
    ) -> list[MatchOutcome]:
        match_context = self._match_context(match_number=match_number, fallback_group=group)
        outcomes = self.pairwise_win_model.group_outcomes(
            home_team,
            away_team,
            match_context=match_context,
            team_power_model=self.team_power_model,
            model_config=self.model_config,
            decisive_band=self.decisive_band,
        )
        return self._validate_group_outcomes(outcomes, match_context=match_context)

    def _knockout_home_win_probability(
        self,
        home_team: str,
        away_team: str,
        match_number: int | None,
    ) -> float:
        match_context = self._match_context(match_number=match_number)
        raw = self.pairwise_win_model.knockout_home_win_probability(
            home_team,
            away_team,
            match_context=match_context,
            team_power_model=self.team_power_model,
            model_config=self.model_config,
            draw_band=self.draw_band,
        )
        return self._clamp_knockout_probability(raw, match_context=match_context)

    def _standing_sort_key(self, standing: TeamStanding) -> tuple:
        team_rank = self._team_profile(standing.team)
        return (
            -standing.points,
            -standing.goal_difference,
            -standing.goals_for,
            -team_rank.rating,
            team_rank.seed_rank,
            standing.team,
        )

    def _third_sort_key(self, standing: TeamStanding) -> tuple:
        return self._standing_sort_key(standing)

    def _copy_table(self, table: dict[str, TeamStanding]) -> dict[str, TeamStanding]:
        return {
            team: TeamStanding(
                team=row.team,
                points=row.points,
                goals_for=row.goals_for,
                goals_against=row.goals_against,
                wins=row.wins,
                draws=row.draws,
                losses=row.losses,
            )
            for team, row in table.items()
        }

    def _table_signature(self, table: dict[str, TeamStanding]) -> tuple:
        return tuple(
            (
                team,
                row.points,
                row.goals_for,
                row.goals_against,
                row.wins,
                row.draws,
                row.losses,
            )
            for team, row in sorted(table.items(), key=lambda item: item[0])
        )

    def _apply_result(self, table: dict[str, TeamStanding], home_team: str, away_team: str, outcome: MatchOutcome) -> None:
        home = table[home_team]
        away = table[away_team]
        home.goals_for += outcome.home_goals
        home.goals_against += outcome.away_goals
        away.goals_for += outcome.away_goals
        away.goals_against += outcome.home_goals

        if outcome.home_goals > outcome.away_goals:
            home.points += 3
            home.wins += 1
            away.losses += 1
        elif outcome.home_goals < outcome.away_goals:
            away.points += 3
            away.wins += 1
            home.losses += 1
        else:
            home.points += 1
            away.points += 1
            home.draws += 1
            away.draws += 1

    def _init_group_table(self, group: str) -> dict[str, TeamStanding]:
        teams = self.resolver.group_to_teams.get(group, [])
        return {team: TeamStanding(team=team) for team in teams}

    def _beam_group_scenarios(
        self,
        group: str,
        beam_width: int,
        max_scenarios: int,
        min_branch_probability: float,
    ) -> list[GroupScenario]:
        matches = self.group_matches.get(group, [])
        if not matches:
            return []

        initial_table = self._init_group_table(group)
        states: list[tuple[dict[str, TeamStanding], float]] = [(initial_table, 1.0)]

        for match_number, home_team, away_team in matches:
            merged: dict[tuple, tuple[dict[str, TeamStanding], float]] = {}
            fixed_result = self._played_result(match_number)
            for table, state_probability in states:
                outcomes: list[tuple[str, str, MatchOutcome]]
                if fixed_result is not None:
                    outcomes = [
                        (
                            fixed_result.home_team,
                            fixed_result.away_team,
                            MatchOutcome(
                                home_goals=fixed_result.home_goals,
                                away_goals=fixed_result.away_goals,
                                probability=1.0,
                            ),
                        )
                    ]
                else:
                    outcomes = [
                        (home_team, away_team, outcome)
                        for outcome in self._group_outcomes(
                            home_team,
                            away_team,
                            match_number=match_number,
                            group=group,
                        )
                    ]

                for outcome_home, outcome_away, outcome in outcomes:
                    branch_probability = state_probability * outcome.probability
                    if branch_probability < min_branch_probability:
                        continue
                    next_table = self._copy_table(table)
                    self._apply_result(next_table, outcome_home, outcome_away, outcome)
                    signature = self._table_signature(next_table)
                    if signature in merged:
                        prev_table, prev_probability = merged[signature]
                        merged[signature] = (prev_table, prev_probability + branch_probability)
                    else:
                        merged[signature] = (next_table, branch_probability)

            states = sorted(merged.values(), key=lambda item: item[1], reverse=True)[:beam_width]
            if not states:
                break

        scenarios: list[GroupScenario] = []
        for table, probability in states:
            standings = sorted(table.values(), key=self._standing_sort_key)
            scenarios.append(
                GroupScenario(
                    group=group,
                    probability=probability,
                    standings=standings,
                    signature=self._table_signature(table),
                )
            )
        scenarios.sort(key=lambda scenario: scenario.probability, reverse=True)
        return scenarios[:max_scenarios]

    def _beam_world_scenarios(
        self,
        group_beam_width: int,
        group_max_scenarios: int,
        world_beam_width: int,
        min_branch_probability: float,
    ) -> list[WorldScenario]:
        groups = sorted(self.resolver.group_to_teams.keys())
        scenarios_by_group = {
            group: self._beam_group_scenarios(
                group=group,
                beam_width=group_beam_width,
                max_scenarios=group_max_scenarios,
                min_branch_probability=min_branch_probability,
            )
            for group in groups
        }

        worlds: list[WorldScenario] = [WorldScenario(probability=1.0, group_scenarios={}, signature=tuple())]
        for group in groups:
            group_scenarios = scenarios_by_group.get(group, [])
            if not group_scenarios:
                return []

            merged: dict[tuple, WorldScenario] = {}
            for world in worlds:
                for group_scenario in group_scenarios:
                    probability = world.probability * group_scenario.probability
                    next_group_scenarios = dict(world.group_scenarios)
                    next_group_scenarios[group] = group_scenario
                    signature = tuple(
                        (letter, next_group_scenarios[letter].signature)
                        for letter in sorted(next_group_scenarios.keys())
                    )
                    if signature in merged:
                        merged[signature].probability += probability
                    else:
                        merged[signature] = WorldScenario(
                            probability=probability,
                            group_scenarios=next_group_scenarios,
                            signature=signature,
                        )

            worlds = sorted(merged.values(), key=lambda item: item.probability, reverse=True)[:world_beam_width]
            if not worlds:
                break

        total = sum(world.probability for world in worlds)
        if total > 0:
            for world in worlds:
                world.probability /= total
        return worlds

    def _parse_group_slot_with_order(self, slot_text: str) -> tuple[list[str], str] | None:
        pattern = r"^Group\s+([A-Z](?:/[A-Z])*)\s+(winners|runners-up|third place)$"
        match = re.match(pattern, slot_text.strip(), flags=re.IGNORECASE)
        if not match:
            return None
        group_letters = self.resolver.expand_groups(match.group(1).upper())
        slot_type = match.group(2).lower()
        return group_letters, slot_type

    def _standing_for_group_slot(self, world: WorldScenario, group: str, slot_type: str) -> TeamStanding:
        scenario = world.group_scenarios[group]
        if slot_type == "winners":
            return scenario.standings[0]
        if slot_type == "runners-up":
            return scenario.standings[1]
        if slot_type == "third place":
            return scenario.standings[2]
        raise ValueError(f"Unsupported slot type: {slot_type}")

    def _best_third_teams(self, world: WorldScenario) -> list[TeamStanding]:
        all_third = [scenario.standings[2] for scenario in world.group_scenarios.values()]
        return sorted(all_third, key=self._third_sort_key)[:8]

    def _group_for_team(self, team: str) -> str | None:
        for group, teams in self.resolver.group_to_teams.items():
            if team in teams:
                return group
        return None

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

    def _assign_third_place_slots_for_world(self, world: WorldScenario) -> dict[tuple[int, str], str]:
        best_third = self._best_third_teams(world)
        best_third_teams = [row.team for row in best_third]
        slots = self._collect_third_place_slots()
        team_group = {team: self._group_for_team(team) for team in best_third_teams}
        priority = {team: idx for idx, team in enumerate(best_third_teams)}

        options: dict[tuple[int, str], list[str]] = {}
        for match_number, side, allowed_groups in slots:
            key = (match_number, side)
            candidates = [team for team in best_third_teams if team_group.get(team) in allowed_groups]
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
        raise ValueError("Unable to assign third-place teams for world scenario.")

    def _slot_team_distribution(
        self,
        slot_text: str,
        match_number: int,
        side: str,
        world: WorldScenario,
        third_assignments: dict[tuple[int, str], str],
        memo: dict[tuple[int, str], dict[str, float]],
    ) -> dict[str, float]:
        group_slot = self._parse_group_slot_with_order(slot_text)
        if group_slot:
            groups, slot_type = group_slot
            if slot_type == "third place":
                assigned = third_assignments[(match_number, side)]
                return {assigned: 1.0}

            contenders = [self._standing_for_group_slot(world, group, slot_type).team for group in groups]
            if len(contenders) == 1:
                return {contenders[0]: 1.0}

            total = 0.0
            dist: dict[str, float] = {}
            for contender in contenders:
                points = self._team_profile(contender).rating
                weight = max(points, self.model_config.slot_contender_min_weight)
                total += weight
                dist[contender] = weight
            return {team: weight / total for team, weight in dist.items()}

        winner_match = re.match(r"^Winner Match\s+(\d+)$", slot_text.strip(), flags=re.IGNORECASE)
        if winner_match:
            ref = int(winner_match.group(1))
            return self._match_winner_distribution(ref, world, third_assignments, memo)

        loser_match = re.match(r"^Loser Match\s+(\d+)$", slot_text.strip(), flags=re.IGNORECASE)
        if loser_match:
            ref = int(loser_match.group(1))
            return self._match_loser_distribution(ref, world, third_assignments, memo)

        return {slot_text.strip(): 1.0}

    def _match_winner_distribution(
        self,
        match_number: int,
        world: WorldScenario,
        third_assignments: dict[tuple[int, str], str],
        memo: dict[tuple[int, str], dict[str, float]],
    ) -> dict[str, float]:
        key = (match_number, "winner")
        if key in memo:
            return memo[key]
        fixed_result = self._played_result(match_number)
        if fixed_result is not None:
            if fixed_result.home_goals == fixed_result.away_goals:
                memo[key] = {}
                return memo[key]
            winner = (
                fixed_result.home_team
                if fixed_result.home_goals > fixed_result.away_goals
                else fixed_result.away_team
            )
            memo[key] = {winner: 1.0}
            return memo[key]
        match = self.match_by_number.get(match_number)
        if not match:
            memo[key] = {}
            return memo[key]
        slots = self.resolver.parse_fixed_matchup(str(match.get("comment") or match.get("matchup") or ""))
        if not slots:
            memo[key] = {}
            return memo[key]

        left_slot, right_slot = slots
        left_dist = self._slot_team_distribution(
            left_slot,
            match_number,
            "left",
            world,
            third_assignments,
            memo,
        )
        right_dist = self._slot_team_distribution(
            right_slot,
            match_number,
            "right",
            world,
            third_assignments,
            memo,
        )
        winners: dict[str, float] = {}
        for home_team, home_probability in left_dist.items():
            for away_team, away_probability in right_dist.items():
                if home_team == away_team:
                    continue
                matchup_probability = home_probability * away_probability
                home_win_probability = self._knockout_home_win_probability(
                    home_team,
                    away_team,
                    match_number=match_number,
                )
                winners[home_team] = winners.get(home_team, 0.0) + matchup_probability * home_win_probability
                winners[away_team] = winners.get(away_team, 0.0) + matchup_probability * (1.0 - home_win_probability)

        memo[key] = winners
        return winners

    def _match_loser_distribution(
        self,
        match_number: int,
        world: WorldScenario,
        third_assignments: dict[tuple[int, str], str],
        memo: dict[tuple[int, str], dict[str, float]],
    ) -> dict[str, float]:
        key = (match_number, "loser")
        if key in memo:
            return memo[key]
        fixed_result = self._played_result(match_number)
        if fixed_result is not None:
            if fixed_result.home_goals == fixed_result.away_goals:
                memo[key] = {}
                return memo[key]
            loser = (
                fixed_result.away_team
                if fixed_result.home_goals > fixed_result.away_goals
                else fixed_result.home_team
            )
            memo[key] = {loser: 1.0}
            return memo[key]
        match = self.match_by_number.get(match_number)
        if not match:
            memo[key] = {}
            return memo[key]
        slots = self.resolver.parse_fixed_matchup(str(match.get("comment") or match.get("matchup") or ""))
        if not slots:
            memo[key] = {}
            return memo[key]

        left_slot, right_slot = slots
        left_dist = self._slot_team_distribution(
            left_slot,
            match_number,
            "left",
            world,
            third_assignments,
            memo,
        )
        right_dist = self._slot_team_distribution(
            right_slot,
            match_number,
            "right",
            world,
            third_assignments,
            memo,
        )
        losers: dict[str, float] = {}
        for home_team, home_probability in left_dist.items():
            for away_team, away_probability in right_dist.items():
                if home_team == away_team:
                    continue
                matchup_probability = home_probability * away_probability
                home_win_probability = self._knockout_home_win_probability(
                    home_team,
                    away_team,
                    match_number=match_number,
                )
                losers[away_team] = losers.get(away_team, 0.0) + matchup_probability * home_win_probability
                losers[home_team] = losers.get(home_team, 0.0) + matchup_probability * (1.0 - home_win_probability)

        memo[key] = losers
        return losers

    def _target_pair_distribution_for_world(
        self,
        match_number: int,
        world: WorldScenario,
    ) -> dict[tuple[str, str], float]:
        match = self.match_by_number.get(match_number)
        if not match:
            return {}
        slots = self.resolver.parse_fixed_matchup(str(match.get("comment") or match.get("matchup") or ""))
        if not slots:
            return {}

        left_slot, right_slot = slots
        third_assignments = self._assign_third_place_slots_for_world(world)
        memo: dict[tuple[int, str], dict[str, float]] = {}

        left_dist = self._slot_team_distribution(
            left_slot,
            match_number,
            "left",
            world,
            third_assignments,
            memo,
        )
        right_dist = self._slot_team_distribution(
            right_slot,
            match_number,
            "right",
            world,
            third_assignments,
            memo,
        )

        pairs: dict[tuple[str, str], float] = {}
        for home_team, home_probability in left_dist.items():
            for away_team, away_probability in right_dist.items():
                if home_team == away_team:
                    continue
                probability = home_probability * away_probability
                if probability <= 0:
                    continue
                pair_key = (home_team, away_team)
                pairs[pair_key] = pairs.get(pair_key, 0.0) + probability
        return pairs

    def predict_matchup_candidates(
        self,
        match_number: int,
        limit: int = 10,
        group_beam_width: int | None = None,
        group_max_scenarios: int | None = None,
        world_beam_width: int | None = None,
        min_branch_probability: float | None = None,
    ) -> list[tuple[str, str, float]]:
        group_beam_width = (
            self.model_config.prediction_group_beam_width
            if group_beam_width is None
            else group_beam_width
        )
        group_max_scenarios = (
            self.model_config.prediction_group_max_scenarios
            if group_max_scenarios is None
            else group_max_scenarios
        )
        world_beam_width = (
            self.model_config.prediction_world_beam_width
            if world_beam_width is None
            else world_beam_width
        )
        min_branch_probability = (
            self.model_config.prediction_min_branch_probability
            if min_branch_probability is None
            else min_branch_probability
        )
        worlds = self._beam_world_scenarios(
            group_beam_width=group_beam_width,
            group_max_scenarios=group_max_scenarios,
            world_beam_width=world_beam_width,
            min_branch_probability=min_branch_probability,
        )
        if not worlds:
            return []

        aggregate: dict[tuple[str, str], float] = {}
        for world in worlds:
            try:
                pair_dist = self._target_pair_distribution_for_world(match_number=match_number, world=world)
            except ValueError:
                continue
            for pair, pair_probability in pair_dist.items():
                aggregate[pair] = aggregate.get(pair, 0.0) + world.probability * pair_probability

        if not aggregate:
            return []

        ranked = sorted(aggregate.items(), key=lambda item: item[1], reverse=True)[:limit]
        total = sum(score for _pair, score in ranked)
        if total <= 0:
            return []
        return [(home, away, score / total) for (home, away), score in ranked]

    # Backward compatibility helper used by older call sites/tests.
    def simulate_tournament(self, match_numbers: Iterable[int] | None = None) -> dict[int, tuple[str, str]]:
        out: dict[int, tuple[str, str]] = {}
        selected_match_numbers = (
            sorted(int(match_number) for match_number in match_numbers)
            if match_numbers is not None
            else sorted(self.match_by_number.keys())
        )
        for match_number in selected_match_numbers:
            candidates = self.predict_matchup_candidates(match_number=match_number, limit=1)
            if not candidates:
                continue
            home_team, away_team, _score = candidates[0]
            out[match_number] = (home_team, away_team)
        return out
