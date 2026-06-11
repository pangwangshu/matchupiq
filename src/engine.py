from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

try:
    from src.live_scores import LiveScoreSnapshotStatus, LiveScoreSnapshotStore
    from src.models import MatchupCandidate, PredictionResponse
    from src.polymarket import (
        GatewayPolymarketSnapshotFetcher,
        HybridPairwiseWinModel,
        PolymarketSnapshotStatus,
        PolymarketSnapshotStore,
    )
    from src.signals import EloStrengthSignal, GroupFormSignal, TravelRestSignal
    from src.team_name_normalization import TeamNameNormalizer
    from src.tournament import MatchResultState, TournamentStructureResolver
    from src.world_ranking import (
        FifaTeamPowerModel,
        PairwiseWinModel,
        RatingPairwiseWinModel,
        TeamPowerModel,
        WorldRankingTournamentSimulator,
    )
except ModuleNotFoundError:
    from live_scores import LiveScoreSnapshotStatus, LiveScoreSnapshotStore
    from models import MatchupCandidate, PredictionResponse
    from polymarket import (
        GatewayPolymarketSnapshotFetcher,
        HybridPairwiseWinModel,
        PolymarketSnapshotStatus,
        PolymarketSnapshotStore,
    )
    from signals import EloStrengthSignal, GroupFormSignal, TravelRestSignal
    from team_name_normalization import TeamNameNormalizer
    from tournament import MatchResultState, TournamentStructureResolver
    from world_ranking import (
        FifaTeamPowerModel,
        PairwiseWinModel,
        RatingPairwiseWinModel,
        TeamPowerModel,
        WorldRankingTournamentSimulator,
    )

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "matches_2026.json"
LIVE_RESULTS_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "live_results_2026.json"
WORLD_CUP_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "worldcup_2026_static.json"
FIFA_RANKING_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "fifa_men_ranking_static.json"
StrengthMode = Literal["fifa", "hybrid", "market"]


class MatchDataProvider(Protocol):
    """Loads the static and live data required by the prediction engine."""

    def load_matches(self) -> dict:
        """Return the current match metadata and any confirmed live results."""
        ...

    def load_live_results(self) -> dict:
        """Return normalized fetched live results, if present."""
        ...

    def load_world_cup_data(self) -> dict:
        """Return the canonical tournament structure payload."""
        ...

    def load_fifa_ranking_data(self) -> dict:
        """Return the ranking snapshot used to seed team strength."""
        ...

    def load_participant_teams(self) -> list[str]:
        """Return the full list of participating team names."""
        ...


class TournamentSimulator(Protocol):
    def predict_matchup_candidates(self, match_number: int, limit: int = 10) -> list[tuple[str, str, float]]:
        """Return ranked matchup candidates for a future or unresolved match."""
        ...


class TournamentSimulatorFactory(Protocol):
    """Builds configured simulator components for the active prediction mode."""

    def create_default_team_power_model(self, fifa_ranking_data: dict) -> TeamPowerModel:
        """Create the default team-strength model for the given ranking payload."""
        ...

    def create_default_pairwise_win_model(
        self,
        canonical_team_names: list[str] | None = None,
    ) -> PairwiseWinModel:
        """Create the default pairwise match model for the configured mode."""
        ...

    def create(
        self,
        world_cup_data: dict,
        team_power_model: TeamPowerModel,
        pairwise_win_model: PairwiseWinModel,
        match_results: dict[int, MatchResultState] | None = None,
    ) -> TournamentSimulator:
        """Assemble a tournament simulator from the supplied model components."""
        ...


@dataclass
class StaticJsonMatchDataProvider:
    """Reads tournament, match, and ranking data from local JSON snapshots."""

    matches_path: Path = DATA_PATH
    live_results_path: Path = LIVE_RESULTS_DATA_PATH
    world_cup_path: Path = WORLD_CUP_DATA_PATH
    fifa_ranking_path: Path = FIFA_RANKING_DATA_PATH

    def load_matches(self) -> dict:
        """Load the UI/API match listing plus any live-result fields."""
        with self.matches_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def load_live_results(self) -> dict:
        """Load normalized fetched score data, ignoring missing or malformed snapshots."""
        if not self.live_results_path.exists():
            return {}
        try:
            with self.live_results_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def load_world_cup_data(self) -> dict:
        """Load the canonical World Cup structure and schedule."""
        with self.world_cup_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def load_fifa_ranking_data(self) -> dict:
        """Load the static FIFA ranking snapshot used by default models."""
        with self.fifa_ranking_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def load_participant_teams(self) -> list[str]:
        """Load the participating team names from the World Cup payload."""
        payload = self.load_world_cup_data()
        participants = payload.get("participants", [])
        return [p["name"] for p in participants if p.get("name")]


@dataclass(frozen=True)
class PredictorRuntimeConfig:
    """Runtime controls for selecting and gating strength signals."""

    strength_mode: StrengthMode = "market"
    market_fresh_ttl_seconds: float = 300.0
    market_serve_stale_ttl_seconds: float = 1800.0
    max_market_age_seconds: float = 900.0
    max_market_spread: float = 0.18
    min_market_liquidity: float = 0.0

    def __post_init__(self) -> None:
        if self.strength_mode not in ("fifa", "hybrid", "market"):
            raise ValueError(f"Unsupported strength mode: {self.strength_mode}")
        for field_name in (
            "market_fresh_ttl_seconds",
            "market_serve_stale_ttl_seconds",
            "max_market_age_seconds",
            "max_market_spread",
            "min_market_liquidity",
        ):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative.")


class WorldRankingSimulatorFactory:
    """Creates the default world-ranking simulator stack used by the app."""

    def __init__(
        self,
        *,
        config: PredictorRuntimeConfig | None = None,
        strength_mode: StrengthMode | None = None,
        use_polymarket_hybrid: bool | None = None,
        polymarket_snapshot_store: PolymarketSnapshotStore | None = None,
    ) -> None:
        if config is not None and strength_mode is not None:
            raise ValueError("Pass either config or strength_mode, not both.")
        if config is None:
            if strength_mode is None and use_polymarket_hybrid is not None:
                strength_mode = "hybrid" if use_polymarket_hybrid else "fifa"
            config = PredictorRuntimeConfig(strength_mode=strength_mode or "market")
        self.config = config
        self.polymarket_snapshot_store = polymarket_snapshot_store

    def _ensure_polymarket_snapshot_store(
        self,
        canonical_team_names: list[str],
    ) -> PolymarketSnapshotStore:
        if self.polymarket_snapshot_store is None:
            normalizer = TeamNameNormalizer.build(canonical_names=canonical_team_names)
            fetcher = GatewayPolymarketSnapshotFetcher(normalizer=normalizer)
            self.polymarket_snapshot_store = PolymarketSnapshotStore(
                fetcher=fetcher,
                fresh_ttl_seconds=self.config.market_fresh_ttl_seconds,
                serve_stale_ttl_seconds=self.config.market_serve_stale_ttl_seconds,
                auto_refresh_on_access=False,
            )
        return self.polymarket_snapshot_store

    def create_default_team_power_model(self, fifa_ranking_data: dict) -> TeamPowerModel:
        """Build the default FIFA-based team power model."""
        return FifaTeamPowerModel(
            fifa_ranking_data=fifa_ranking_data,
            default_rank_for_unlisted_team=120,
            default_points_fallback=1400.0,
        )

    def create_default_pairwise_win_model(
        self,
        canonical_team_names: list[str] | None = None,
    ) -> PairwiseWinModel:
        """Build the default pairwise model, optionally enabling hybrid market data."""
        fallback = RatingPairwiseWinModel()
        if self.config.strength_mode == "fifa" or not canonical_team_names:
            return fallback

        return HybridPairwiseWinModel(
            snapshot_store=self._ensure_polymarket_snapshot_store(canonical_team_names),
            fallback=fallback,
            max_market_age_seconds=self.config.max_market_age_seconds,
            max_market_spread=self.config.max_market_spread,
            min_market_liquidity=self.config.min_market_liquidity,
            mode_label=self.config.strength_mode,
        )

    def refresh_polymarket_snapshot(
        self,
        canonical_team_names: list[str],
    ) -> PolymarketSnapshotStore:
        """Force-refresh the Polymarket snapshot used by hybrid predictions."""
        store = self._ensure_polymarket_snapshot_store(canonical_team_names)
        store.refresh_now()
        return store

    def polymarket_snapshot_status(
        self,
        canonical_team_names: list[str],
    ) -> PolymarketSnapshotStatus | None:
        """Return market cache status when the active mode can use Polymarket."""
        if self.config.strength_mode == "fifa":
            return None
        return self._ensure_polymarket_snapshot_store(canonical_team_names).status()

    def create(
        self,
        world_cup_data: dict,
        team_power_model: TeamPowerModel,
        pairwise_win_model: PairwiseWinModel,
        match_results: dict[int, MatchResultState] | None = None,
    ) -> WorldRankingTournamentSimulator:
        """Create a world-ranking simulator with the supplied runtime dependencies."""
        return WorldRankingTournamentSimulator(
            world_cup_data=world_cup_data,
            team_power_model=team_power_model,
            pairwise_win_model=pairwise_win_model,
            match_results=match_results,
        )


class MatchupPredictor:
    """High-level entry point for building ranked matchup predictions."""

    def __init__(
        self,
        data_provider: MatchDataProvider | None = None,
        simulator_factory: TournamentSimulatorFactory | None = None,
        runtime_config: PredictorRuntimeConfig | None = None,
        live_score_snapshot_store: LiveScoreSnapshotStore | None = None,
    ) -> None:
        self.data_provider = data_provider or StaticJsonMatchDataProvider()
        if simulator_factory is not None and runtime_config is not None:
            raise ValueError("Pass either simulator_factory or runtime_config, not both.")
        self.simulator_factory = simulator_factory or WorldRankingSimulatorFactory(config=runtime_config)
        self.live_score_snapshot_store = live_score_snapshot_store
        self.last_signal_status: dict | None = None
        self.group_form = GroupFormSignal()
        self.elo = EloStrengthSignal()
        self.travel_rest = TravelRestSignal()

    def _load_matches(self) -> dict:
        return self.data_provider.load_matches()

    def _load_live_results(self) -> dict:
        try:
            payload = self.data_provider.load_live_results()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _load_participant_teams(self) -> list[str]:
        return self.data_provider.load_participant_teams()

    def _load_world_cup_data(self) -> dict:
        return self.data_provider.load_world_cup_data()

    def _load_fifa_ranking_data(self) -> dict:
        return self.data_provider.load_fifa_ranking_data()

    def _canonical_team_names(self, world_cup_data: dict) -> list[str]:
        return [
            str(participant.get("name"))
            for participant in world_cup_data.get("participants", [])
            if isinstance(participant, dict) and participant.get("name")
        ]

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

    def _states_from_result_payload(self, payload: dict) -> dict[int, MatchResultState]:
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

    def _load_match_results_state(self) -> dict[int, MatchResultState]:
        live_payload = self._load_live_results()
        live_results = live_payload.get("results", {}) if isinstance(live_payload, dict) else {}
        out = self._states_from_result_payload(live_results if isinstance(live_results, dict) else {})
        manual = self._states_from_result_payload(self._load_matches())
        out.update(manual)
        return out

    def _resolver(self) -> TournamentStructureResolver:
        world_cup = self._load_world_cup_data()
        return TournamentStructureResolver(
            groups=world_cup.get("groups", []),
            schedule=world_cup.get("schedule", []),
        )

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
        team_power_model = self.simulator_factory.create_default_team_power_model(fifa_ranking_data)
        canonical_team_names = self._canonical_team_names(world_cup_data)
        pairwise_win_model = self.simulator_factory.create_default_pairwise_win_model(
            canonical_team_names=canonical_team_names,
        )
        if hasattr(pairwise_win_model, "reset_usage"):
            pairwise_win_model.reset_usage()
        simulator = self.simulator_factory.create(
            world_cup_data=world_cup_data,
            team_power_model=team_power_model,
            pairwise_win_model=pairwise_win_model,
            match_results=self._load_match_results_state(),
        )
        predicted = simulator.predict_matchup_candidates(
            match_number=match_number,
            limit=limit,
        )
        if not predicted:
            return None

        signal_status = self._build_signal_status(
            world_cup_data=world_cup_data,
            pairwise_win_model=pairwise_win_model,
        )
        self.last_signal_status = signal_status
        out: list[MatchupCandidate] = []
        for home_team, away_team, probability in predicted:
            reason = (
                "Predicted via scenario-search simulation "
                "(group standings + knockout outcome branching)."
            )
            if signal_status.get("fallback_visible"):
                reason = f"{reason} Market mode used FIFA fallback for some unresolved match branches."
            out.append(
                MatchupCandidate(
                    home_team=home_team,
                    away_team=away_team,
                    score=round(probability, 4),
                    reason=reason,
                )
            )
        return out

    def _build_signal_status(
        self,
        *,
        world_cup_data: dict,
        pairwise_win_model: PairwiseWinModel,
    ) -> dict:
        if isinstance(self.simulator_factory, WorldRankingSimulatorFactory):
            status: dict = {
                "strength_mode": self.simulator_factory.config.strength_mode,
                "market_hits": 0,
                "fallback_hits": 0,
                "fallback_reasons": {},
                "fallback_visible": False,
            }
            if hasattr(pairwise_win_model, "usage_summary"):
                status.update(pairwise_win_model.usage_summary())
            snapshot_status = self.simulator_factory.polymarket_snapshot_status(
                self._canonical_team_names(world_cup_data)
            )
            if snapshot_status is not None:
                status["polymarket_snapshot"] = snapshot_status.to_dict()
            return status
        return {"strength_mode": "custom"}

    def refresh_polymarket_snapshot(self) -> None:
        """Refresh the market snapshot when hybrid predictions are enabled."""
        world_cup_data = self._load_world_cup_data()
        canonical_team_names = self._canonical_team_names(world_cup_data)
        if isinstance(self.simulator_factory, WorldRankingSimulatorFactory):
            self.simulator_factory.refresh_polymarket_snapshot(canonical_team_names)

    def _ensure_live_score_snapshot_store(self) -> LiveScoreSnapshotStore:
        if self.live_score_snapshot_store is None:
            self.live_score_snapshot_store = LiveScoreSnapshotStore(snapshot_path=LIVE_RESULTS_DATA_PATH)
        return self.live_score_snapshot_store

    def refresh_live_scores(self) -> dict:
        """Fetch and persist the latest normalized World Cup score snapshot."""
        world_cup_data = self._load_world_cup_data()
        normalizer = TeamNameNormalizer.build(canonical_names=self._canonical_team_names(world_cup_data))
        store = self._ensure_live_score_snapshot_store()
        snapshot = store.refresh_now(world_cup_data=world_cup_data, normalizer=normalizer)
        return {
            "provider": snapshot.provider,
            "has_snapshot": True,
            "fetched_at_epoch": snapshot.fetched_at_epoch,
            "matched_count": len(snapshot.results),
            "completed_count": snapshot.completed_count,
            "unmatched_count": len(snapshot.unmatched_provider_matches),
            "last_refresh_attempt_at": snapshot.fetched_at_epoch,
            "last_refresh_error": None,
        }

    def live_score_status(self) -> dict:
        """Return current live-score cache state for UI/API status surfaces."""
        status: LiveScoreSnapshotStatus = self._ensure_live_score_snapshot_store().status()
        return status.to_dict()

    def polymarket_snapshot_status(self) -> dict | None:
        """Return current Polymarket cache state for UI/API status surfaces."""
        world_cup_data = self._load_world_cup_data()
        canonical_team_names = self._canonical_team_names(world_cup_data)
        if isinstance(self.simulator_factory, WorldRankingSimulatorFactory):
            status = self.simulator_factory.polymarket_snapshot_status(canonical_team_names)
            return None if status is None else status.to_dict()
        return None

    def predict(self, match_id: str) -> PredictionResponse:
        """Return the ranked prediction response for the requested match."""
        world_ranking_candidates = None
        try:
            world_ranking_candidates = self._build_world_ranking_candidates(match_id=match_id, limit=10)
        except Exception:
            world_ranking_candidates = None

        if world_ranking_candidates is not None:
            ranked = world_ranking_candidates
        else:
            self.last_signal_status = {"strength_mode": "baseline"}
            ranked = self._build_baseline_candidates(match_id=match_id, limit=10)
        return PredictionResponse(
            match_id=match_id,
            status="predicted",
            top_candidates=ranked,
            signal_status=self.last_signal_status,
        )
