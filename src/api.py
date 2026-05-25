from __future__ import annotations

from fastapi import FastAPI, HTTPException

try:
    from src.engine import MatchupPredictor
    from src.models import PredictionRequest, PredictionResponse
except ModuleNotFoundError:
    from engine import MatchupPredictor
    from models import PredictionRequest, PredictionResponse

app = FastAPI(title="Which Matchup", version="0.1.0")
predictor = MatchupPredictor()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/predict", response_model=PredictionResponse)
def predict(payload: PredictionRequest) -> PredictionResponse:
    try:
        return predictor.predict(payload.match_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
