from __future__ import annotations

import json
from pathlib import Path

from src.models import MatchupCandidate, PredictionResponse
from src.signals import EloStrengthSignal, GroupFormSignal, SignalContext, TravelRestSignal

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "matches_2026.json"


class MatchupPredictor:
    def __init__(self) -> None:
        self.group_form = GroupFormSignal()
        self.elo = EloStrengthSignal()
        self.travel_rest = TravelRestSignal()

    def _load_matches(self) -> dict:
        with DATA_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)

    def predict(self, match_id: str) -> PredictionResponse:
        matches = self._load_matches()
        match = matches.get(match_id)
        if not match:
            raise ValueError(f"Unknown match_id: {match_id}")

        if match["status"] in {"played", "determined"}:
            confirmed = MatchupCandidate(
                home_team=match["confirmed_home"],
                away_team=match["confirmed_away"],
                score=1.0,
                reason="Matchup confirmed by tournament results.",
            )
            return PredictionResponse(
                match_id=match_id,
                status="confirmed",
                confirmed_matchup=confirmed,
                top_candidates=[],
            )

        candidates: list[MatchupCandidate] = []
        for entry in match["candidate_matchups"]:
            context = SignalContext(
                match_id=match_id,
                home_slot_team=entry["home"],
                away_slot_team=entry["away"],
            )

            form_score = self.group_form.score(context)
            elo_score = self.elo.score(context)
            rest_score = self.travel_rest.score(context)

            # Weighted score: tunable once live signals are wired.
            score = (0.45 * form_score) + (0.40 * elo_score) + (0.15 * rest_score)

            candidates.append(
                MatchupCandidate(
                    home_team=entry["home"],
                    away_team=entry["away"],
                    score=round(score, 4),
                    reason=(
                        f"form={form_score:.3f}, elo={elo_score:.3f}, travel_rest={rest_score:.3f}"
                    ),
                )
            )

        ranked = sorted(candidates, key=lambda c: c.score, reverse=True)[:10]
        return PredictionResponse(
            match_id=match_id,
            status="predicted",
            top_candidates=ranked,
        )
