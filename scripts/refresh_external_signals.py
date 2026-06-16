from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.engine import (
    MatchupPredictor,
    PredictorRuntimeConfig,
    StaticJsonMatchDataProvider,
    WorldRankingSimulatorFactory,
)
from src.external_signal_refresh import (
    build_live_score_validation_summary,
    build_polymarket_validation_summary,
    compare_prediction_responses,
    load_persisted_polymarket_snapshot,
    summarize_prediction_response,
)
from src.live_scores import FootballDataLiveScoreFetcher, build_live_score_snapshot
from src.polymarket import (
    DEFAULT_POLYMARKET_CACHE_PATH,
    GatewayPolymarketSnapshotFetcher,
    PolymarketSnapshot,
    PolymarketSnapshotFetcher,
    PolymarketSnapshotStore,
)
from src.prediction_cache import PredictionCacheService
from src.team_name_normalization import TeamNameNormalizer


class _FixedSnapshotFetcher(PolymarketSnapshotFetcher):
    def __init__(self, snapshot: PolymarketSnapshot) -> None:
        self.snapshot = snapshot

    def fetch_snapshot(self) -> PolymarketSnapshot:
        return self.snapshot


class _LiveResultsOverrideProvider(StaticJsonMatchDataProvider):
    def __init__(self, live_results_payload: dict | None = None) -> None:
        super().__init__()
        self._live_results_payload = live_results_payload

    def load_live_results(self) -> dict:
        if self._live_results_payload is None:
            return super().load_live_results()
        return self._live_results_payload


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate, refresh, and compare external prediction signals.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true", help="Check external providers without writing files.")
    mode.add_argument("--apply", action="store_true", help="Persist refreshed external snapshots after validation.")
    parser.add_argument(
        "--compare-match",
        default=None,
        help="Optional match number to compare before vs after signals, e.g. 82.",
    )
    return parser


def _build_predictor_with_overrides(
    *,
    live_results_payload: dict | None = None,
    polymarket_snapshot: PolymarketSnapshot | None = None,
) -> MatchupPredictor:
    provider = _LiveResultsOverrideProvider(live_results_payload=live_results_payload)
    if polymarket_snapshot is None:
        return MatchupPredictor(data_provider=provider)

    store = PolymarketSnapshotStore(
        fetcher=_FixedSnapshotFetcher(polymarket_snapshot),
        cache_path=None,
        auto_refresh_on_access=False,
    )
    store.refresh_now()
    return MatchupPredictor(
        data_provider=provider,
        simulator_factory=WorldRankingSimulatorFactory(
            config=PredictorRuntimeConfig(strength_mode="market"),
            polymarket_snapshot_store=store,
        ),
    )


def generate_report(*, compare_match: str | None, apply: bool) -> dict:
    baseline_predictor = MatchupPredictor()
    world_cup_data = baseline_predictor._load_world_cup_data()
    canonical_names = baseline_predictor._canonical_team_names(world_cup_data)
    normalizer = TeamNameNormalizer.build(canonical_names=canonical_names)

    stored_snapshot = load_persisted_polymarket_snapshot(DEFAULT_POLYMARKET_CACHE_PATH)
    baseline_market_status = baseline_predictor.polymarket_snapshot_status()
    baseline_score_status = baseline_predictor.live_score_status()

    baseline_prediction = None
    if compare_match is not None:
        baseline_prediction = baseline_predictor.predict(compare_match)

    market_fetcher = GatewayPolymarketSnapshotFetcher(normalizer=normalizer)
    market_events = market_fetcher.fetch_events()
    live_market_snapshot = market_fetcher.snapshot_from_events(market_events)
    market_validation = build_polymarket_validation_summary(
        world_cup_data,
        market_events,
        live_market_snapshot,
        stored_snapshot=stored_snapshot,
    )

    score_fetcher = FootballDataLiveScoreFetcher()
    provider_matches = score_fetcher.fetch_matches()
    live_score_snapshot = build_live_score_snapshot(
        provider_matches=provider_matches,
        world_cup_data=world_cup_data,
        normalizer=normalizer,
    )
    live_score_validation = build_live_score_validation_summary(provider_matches, live_score_snapshot)

    report: dict[str, object] = {
        "mode": "apply" if apply else "validate_only",
        "baseline": {
            "market_status": baseline_market_status,
            "score_status": baseline_score_status,
            "stored_polymarket_market_count": (
                len(stored_snapshot.market_selections_by_pair) if stored_snapshot is not None else 0
            ),
        },
        "market_validation": market_validation,
        "live_score_validation": live_score_validation,
    }

    if compare_match is not None and baseline_prediction is not None:
        fresh_market_prediction = _build_predictor_with_overrides(
            polymarket_snapshot=live_market_snapshot,
        ).predict(compare_match)
        fresh_scores_prediction = _build_predictor_with_overrides(
            live_results_payload=live_score_snapshot.to_dict(),
        ).predict(compare_match)
        fully_fresh_prediction = _build_predictor_with_overrides(
            live_results_payload=live_score_snapshot.to_dict(),
            polymarket_snapshot=live_market_snapshot,
        ).predict(compare_match)
        report["comparison"] = {
            "match_id": compare_match,
            "baseline": summarize_prediction_response(baseline_prediction),
            "fresh_market_only": compare_prediction_responses(baseline_prediction, fresh_market_prediction),
            "fresh_scores_only": compare_prediction_responses(baseline_prediction, fresh_scores_prediction),
            "fresh_market_and_scores": compare_prediction_responses(
                baseline_prediction,
                fully_fresh_prediction,
            ),
        }

    if apply:
        apply_predictor = MatchupPredictor()
        prediction_cache = PredictionCacheService(predictor=apply_predictor)
        apply_predictor.refresh_polymarket_snapshot()
        live_score_status = apply_predictor.refresh_live_scores()
        prediction_cache.clear()

        apply_report: dict[str, object] = {
            "polymarket_status": apply_predictor.polymarket_snapshot_status(),
            "live_score_status": live_score_status,
            "prediction_cache_cleared": True,
        }
        if compare_match is not None:
            apply_report["post_refresh_prediction"] = summarize_prediction_response(
                apply_predictor.predict(compare_match)
            )
        report["apply_result"] = apply_report

    return report


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    report = generate_report(compare_match=args.compare_match, apply=bool(args.apply))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
