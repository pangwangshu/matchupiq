from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PredictionRequest(BaseModel):
    match_id: str = Field(..., description="FIFA match id, e.g. 400021525")


class MatchupCandidate(BaseModel):
    home_team: str
    away_team: str
    score: float
    reason: str


class PredictionResponse(BaseModel):
    match_id: str
    status: Literal["confirmed", "predicted"]
    confirmed_matchup: MatchupCandidate | None = None
    top_candidates: list[MatchupCandidate] = Field(default_factory=list)
    signal_status: dict[str, Any] | None = None
