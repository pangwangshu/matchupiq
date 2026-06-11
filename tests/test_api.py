from __future__ import annotations

from fastapi.testclient import TestClient

import src.api as api_module
from src.models import MatchupCandidate, PredictionResponse


def _client_without_startup_refresh(monkeypatch) -> TestClient:
    monkeypatch.setattr(api_module, "refresh_market_signal_on_startup", lambda: None)
    monkeypatch.setattr(api_module, "refresh_live_scores_on_startup", lambda: None)
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


def test_startup_refreshes_live_scores_and_clears_cache(monkeypatch) -> None:
    calls = []

    class StubPredictor:
        def refresh_live_scores(self) -> dict:
            calls.append("refresh")
            return {}

    class StubCacheService:
        def clear(self) -> None:
            calls.append("clear")

    monkeypatch.setattr(api_module, "predictor", StubPredictor())
    monkeypatch.setattr(api_module, "prediction_cache", StubCacheService())

    api_module.refresh_live_scores_on_startup()

    assert calls == ["refresh", "clear"]


def test_startup_live_score_refresh_failure_does_not_crash(monkeypatch) -> None:
    class StubPredictor:
        def refresh_live_scores(self) -> dict:
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(api_module, "predictor", StubPredictor())

    api_module.refresh_live_scores_on_startup()


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


def test_refresh_scores_endpoint_clears_prediction_cache(monkeypatch) -> None:
    calls = []

    class StubPredictor:
        def refresh_live_scores(self) -> dict:
            calls.append("refresh")
            return {
                "provider": "football-data.org",
                "has_snapshot": True,
                "matched_count": 1,
                "completed_count": 1,
                "unmatched_count": 0,
            }

    class StubCacheService:
        def clear(self) -> None:
            calls.append("clear")

    monkeypatch.setattr(api_module, "predictor", StubPredictor())
    monkeypatch.setattr(api_module, "prediction_cache", StubCacheService())
    client = _client_without_startup_refresh(monkeypatch)

    response = client.post("/refresh-scores")

    assert response.status_code == 200
    assert response.json()["matched_count"] == 1
    assert calls == ["refresh", "clear"]


def test_refresh_scores_endpoint_maps_provider_error_to_502(monkeypatch) -> None:
    class StubPredictor:
        def refresh_live_scores(self) -> dict:
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(api_module, "predictor", StubPredictor())
    client = _client_without_startup_refresh(monkeypatch)

    response = client.post("/refresh-scores")

    assert response.status_code == 502
    assert response.json()["detail"] == "provider unavailable"


def test_score_status_endpoint(monkeypatch) -> None:
    class StubPredictor:
        def live_score_status(self) -> dict:
            return {
                "provider": "football-data.org",
                "has_snapshot": True,
                "matched_count": 2,
                "completed_count": 1,
                "unmatched_count": 3,
                "last_refresh_error": None,
            }

    monkeypatch.setattr(api_module, "predictor", StubPredictor())
    client = _client_without_startup_refresh(monkeypatch)

    response = client.get("/score-status")

    assert response.status_code == 200
    assert response.json()["matched_count"] == 2
    assert response.json()["unmatched_count"] == 3
