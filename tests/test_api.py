from __future__ import annotations

from fastapi.testclient import TestClient

import src.api as api_module
from src.models import MatchupCandidate, PredictionResponse


def test_health() -> None:
    client = TestClient(api_module.app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_predict_success(monkeypatch) -> None:
    class StubCacheService:
        def get_prediction(self, match_id: str):
            return PredictionResponse(
                match_id=match_id,
                status="predicted",
                top_candidates=[
                    MatchupCandidate(
                        home_team="Mexico",
                        away_team="South Africa",
                        score=1.0,
                        reason="API serialization test.",
                    )
                ],
            )

    monkeypatch.setattr(api_module, "prediction_cache", StubCacheService())
    client = TestClient(api_module.app)
    response = client.post("/predict", json={"match_id": "74"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["match_id"] == "74"
    assert payload["status"] == "predicted"
    assert isinstance(payload["top_candidates"], list)
    assert len(payload["top_candidates"]) <= 10


def test_predict_maps_value_error_to_404(monkeypatch) -> None:
    class StubCacheService:
        def get_prediction(self, match_id: str):
            raise ValueError(f"missing match id: {match_id}")

    monkeypatch.setattr(api_module, "prediction_cache", StubCacheService())
    client = TestClient(api_module.app)
    response = client.post("/predict", json={"match_id": "999"})

    assert response.status_code == 404
    assert response.json()["detail"] == "missing match id: 999"
