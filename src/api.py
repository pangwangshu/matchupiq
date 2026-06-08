from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

try:
    from src.engine import MatchupPredictor
    from src.models import PredictionRequest, PredictionResponse
    from src.prediction_cache import PredictionCacheService
except ModuleNotFoundError:
    from engine import MatchupPredictor
    from models import PredictionRequest, PredictionResponse
    from prediction_cache import PredictionCacheService

logger = logging.getLogger(__name__)

predictor = MatchupPredictor()
prediction_cache = PredictionCacheService(predictor=predictor)


def refresh_market_signal_on_startup() -> None:
    """Best-effort startup refresh for the latest Polymarket snapshot."""
    try:
        predictor.refresh_polymarket_snapshot()
    except Exception:
        logger.exception("startup_polymarket_refresh_failed")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    refresh_market_signal_on_startup()
    yield


app = FastAPI(title="Which Matchup", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/predict", response_model=PredictionResponse)
def predict(payload: PredictionRequest) -> PredictionResponse:
    try:
        return prediction_cache.get_prediction(payload.match_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
