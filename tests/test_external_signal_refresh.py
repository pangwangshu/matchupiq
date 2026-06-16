from __future__ import annotations

from src.external_signal_refresh import (
    build_live_score_validation_summary,
    build_polymarket_validation_summary,
    compare_prediction_responses,
)
from src.live_scores import LiveScoreSnapshot, NormalizedLiveResult
from src.models import MatchupCandidate, PredictionResponse
from src.polymarket import PolymarketMarketSelection, PolymarketSnapshot, ThreeWayProbability


def test_build_live_score_validation_summary_reports_counts_and_samples() -> None:
    snapshot = LiveScoreSnapshot(
        provider="football-data.org",
        fetched_at_epoch=1234.0,
        results={
            1: NormalizedLiveResult(
                match_number=1,
                status="completed",
                played=True,
                home_team="Mexico",
                away_team="South Africa",
                home_goals=2,
                away_goals=0,
            ),
            2: NormalizedLiveResult(
                match_number=2,
                status="pending",
                played=False,
                home_team="South Korea",
                away_team="Czech Republic",
                home_goals=None,
                away_goals=None,
            ),
        },
        unmatched_provider_matches=[{"provider_match_id": 999, "reason": "team_name_not_recognized"}],
    )

    summary = build_live_score_validation_summary([{"id": 1}, {"id": 2}, {"id": 3}], snapshot)

    assert summary["provider_match_count"] == 3
    assert summary["matched_count"] == 2
    assert summary["completed_count"] == 1
    assert summary["unmatched_count"] == 1
    assert summary["completed_sample"][0]["match_number"] == 1
    assert summary["unmatched_sample"][0]["provider_match_id"] == 999


def test_build_polymarket_validation_summary_flags_coverage_drop() -> None:
    world_cup_data = {
        "participants": [{"name": "Mexico"}, {"name": "South Africa"}],
        "schedule": [
            {
                "match_number": 1,
                "stage": "group_stage",
                "matchup": "Mexico vs South Africa",
            }
        ],
    }
    live_snapshot = PolymarketSnapshot(
        fetched_at_epoch=1234.0,
        events_seen=1,
        market_selections_by_pair={
            (
                "Mexico",
                "South Africa",
            ): PolymarketMarketSelection(
                probabilities=ThreeWayProbability(home_win=0.5, draw=0.25, away_win=0.25),
                max_spread=0.02,
                liquidity=100.0,
                updated_at_epoch=1200.0,
            )
        },
    )
    stored_snapshot = PolymarketSnapshot(
        fetched_at_epoch=1200.0,
        events_seen=2,
        market_selections_by_pair={
            ("Mexico", "South Africa"): PolymarketMarketSelection(
                probabilities=ThreeWayProbability(home_win=0.5, draw=0.25, away_win=0.25),
                max_spread=0.02,
                liquidity=100.0,
                updated_at_epoch=1100.0,
            ),
            ("South Africa", "Mexico"): PolymarketMarketSelection(
                probabilities=ThreeWayProbability(home_win=0.25, draw=0.25, away_win=0.5),
                max_spread=0.02,
                liquidity=100.0,
                updated_at_epoch=1100.0,
            ),
        },
    )

    summary = build_polymarket_validation_summary(
        world_cup_data,
        [
            {
                "title": "Mexico vs. South Africa",
                "markets": [
                    {"bestBidQuote": {"value": "0.48"}, "bestAskQuote": {"value": "0.52"}},
                    {"bestBidQuote": {"value": "0.24"}, "bestAskQuote": {"value": "0.26"}},
                    {"bestBidQuote": {"value": "0.24"}, "bestAskQuote": {"value": "0.26"}},
                ],
            }
        ],
        live_snapshot,
        stored_snapshot=stored_snapshot,
    )

    assert summary["events_seen"] == 1
    assert summary["market_count"] == 1
    assert summary["stored_market_count"] == 2
    assert summary["market_count_delta_vs_stored"] == -1
    assert summary["normalized_group_pair_coverage_count"] == 1
    assert summary["has_knockout_like_events"] is False
    assert any("live market coverage is lower" in warning for warning in summary["warnings"])


def test_compare_prediction_responses_returns_expected_shape() -> None:
    before = PredictionResponse(
        match_id="82",
        status="predicted",
        signal_status={"strength_mode": "market"},
        top_candidates=[
            MatchupCandidate(home_team="Belgium", away_team="Senegal", score=0.4, reason="before"),
            MatchupCandidate(home_team="Belgium", away_team="Ivory Coast", score=0.3, reason="before"),
        ],
    )
    after = PredictionResponse(
        match_id="82",
        status="predicted",
        signal_status={"strength_mode": "market"},
        top_candidates=[
            MatchupCandidate(home_team="Belgium", away_team="Czech Republic", score=0.9, reason="after"),
            MatchupCandidate(home_team="Belgium", away_team="Senegal", score=0.05, reason="after"),
        ],
    )

    summary = compare_prediction_responses(before, after)

    assert summary["before"]["match_id"] == "82"
    assert summary["after"]["top_candidates"][0]["away"] == "Czech Republic"
    assert summary["changed_pair_count"] == 3
    assert summary["largest_changes"][0]["away"] == "Czech Republic"
