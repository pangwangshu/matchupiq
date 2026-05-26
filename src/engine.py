from __future__ import annotations

import json
import random
from pathlib import Path

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

    def _build_baseline_candidates(self, match_id: str, limit: int = 10) -> list[MatchupCandidate]:
        teams = self._load_participant_teams()
        if len(teams) < 2:
            raise ValueError("Not enough participant teams to build baseline predictions.")

        all_pairs: list[tuple[str, str]] = []
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
                    reason="Baseline UI test: matchup can be any two different participant teams.",
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
