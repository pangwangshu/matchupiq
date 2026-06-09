from __future__ import annotations

from fastapi.testclient import TestClient

import src.api as api_module
from src.models import MatchupCandidate, PredictionResponse


def _client_without_startup_refresh(monkeypatch) -> TestClient:
    monkeypatch.setattr(api_module, "refresh_market_signal_on_startup", lambda: None)
    return TestClient(api_module.app)


def test_health(monkeypatch) -> None:
    client = _client_without_startup_refresh(monkeypatch)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_startup_refreshes_market_signal(monkeypatch) -> None:
    calls = []

    class StubPredictor:
        def refresh_polymarket_snapshot(self) -> None:
            calls.append("refresh")

    monkeypatch.setattr(api_module, "predictor", StubPredictor())

    api_module.refresh_market_signal_on_startup()

    assert calls == ["refresh"]


def test_startup_refresh_failure_does_not_crash(monkeypatch) -> None:
    class StubPredictor:
        def refresh_polymarket_snapshot(self) -> None:
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(api_module, "predictor", StubPredictor())

    api_module.refresh_market_signal_on_startup()


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
    client = _client_without_startup_refresh(monkeypatch)
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
    client = _client_without_startup_refresh(monkeypatch)
    response = client.post("/predict", json={"match_id": "999"})

    assert response.status_code == 404
    assert response.json()["detail"] == "missing match id: 999"
