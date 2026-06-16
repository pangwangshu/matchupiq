from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from src.live_scores import LiveScoreSnapshot
    from src.models import PredictionResponse
    from src.polymarket import PolymarketSnapshot
    from src.team_name_normalization import TeamNameNormalizer
except ModuleNotFoundError:
    from live_scores import LiveScoreSnapshot
    from models import PredictionResponse
    from polymarket import PolymarketSnapshot
    from team_name_normalization import TeamNameNormalizer


def summarize_prediction_response(
    response: PredictionResponse,
    *,
    top_n: int = 5,
) -> dict[str, Any]:
    return {
        "match_id": response.match_id,
        "status": response.status,
        "signal_status": response.signal_status,
        "top_candidates": [
            {
                "home": candidate.home_team,
                "away": candidate.away_team,
                "score": candidate.score,
            }
            for candidate in response.top_candidates[:top_n]
        ],
    }


def compare_prediction_responses(
    before: PredictionResponse,
    after: PredictionResponse,
    *,
    top_n: int = 5,
    largest_change_limit: int = 10,
) -> dict[str, Any]:
    before_pairs = {
        (candidate.home_team, candidate.away_team): candidate.score for candidate in before.top_candidates
    }
    after_pairs = {
        (candidate.home_team, candidate.away_team): candidate.score for candidate in after.top_candidates
    }

    changes: list[dict[str, Any]] = []
    for pair in sorted(set(before_pairs) | set(after_pairs)):
        before_score = before_pairs.get(pair, 0.0)
        after_score = after_pairs.get(pair, 0.0)
        delta = round(after_score - before_score, 6)
        if delta == 0:
            continue
        changes.append(
            {
                "home": pair[0],
                "away": pair[1],
                "before_score": before_score,
                "after_score": after_score,
                "delta": delta,
            }
        )

    ordered_changes = sorted(changes, key=lambda item: abs(item["delta"]), reverse=True)
    return {
        "before": summarize_prediction_response(before, top_n=top_n),
        "after": summarize_prediction_response(after, top_n=top_n),
        "changed_pair_count": len(changes),
        "largest_changes": ordered_changes[:largest_change_limit],
    }


def build_live_score_validation_summary(
    provider_matches: list[dict[str, Any]],
    snapshot: LiveScoreSnapshot,
    *,
    completed_sample_size: int = 10,
) -> dict[str, Any]:
    completed = [
        result.to_dict()
        for result in sorted(snapshot.results.values(), key=lambda result: result.match_number)
        if result.played
    ]
    return {
        "provider": snapshot.provider,
        "provider_match_count": len(provider_matches),
        "matched_count": len(snapshot.results),
        "completed_count": snapshot.completed_count,
        "unmatched_count": len(snapshot.unmatched_provider_matches),
        "completed_sample": completed[:completed_sample_size],
        "unmatched_sample": snapshot.unmatched_provider_matches[:completed_sample_size],
    }


def build_polymarket_validation_summary(
    world_cup_data: dict[str, Any],
    events: list[dict[str, Any]],
    snapshot: PolymarketSnapshot,
    *,
    stored_snapshot: PolymarketSnapshot | None = None,
    missing_sample_size: int = 8,
) -> dict[str, Any]:
    canonical_names = [
        str(participant.get("name"))
        for participant in world_cup_data.get("participants", [])
        if isinstance(participant, dict) and participant.get("name")
    ]
    normalizer = TeamNameNormalizer.build(canonical_names=canonical_names)
    schedule = world_cup_data.get("schedule", [])
    group_matches = [
        match for match in schedule if isinstance(match, dict) and match.get("stage") == "group_stage"
    ]

    normalized_schedule_pairs = {
        normalized_pair
        for match in group_matches
        for pair in [_parse_schedule_matchup_to_pair(str(match.get("matchup", "")))]
        if pair is not None
        for normalized_pair in [_normalize_canonical_pair(pair, normalizer)]
        if normalized_pair is not None
    }
    normalized_event_pairs = {
        normalized_pair
        for event in events
        for pair in [_parse_polymarket_title_to_pair(str(event.get("title", "")))]
        if pair is not None
        for normalized_pair in [_normalize_canonical_pair(pair, normalizer)]
        if normalized_pair is not None
    }

    normalized_coverage_pairs = normalized_schedule_pairs & normalized_event_pairs
    normalized_missing_pairs = sorted(normalized_schedule_pairs - normalized_event_pairs)
    knockout_like_titles = [
        str(event.get("title", ""))
        for event in events
        if _is_knockout_like_event_title(str(event.get("title", "")))
    ]

    quality = _probe_market_quality(events)
    stored_market_count = (
        len(stored_snapshot.market_selections_by_pair) if stored_snapshot is not None else None
    )
    market_count_delta_vs_stored = (
        None
        if stored_market_count is None
        else len(snapshot.market_selections_by_pair) - stored_market_count
    )

    warnings: list[str] = []
    if stored_market_count is not None and len(snapshot.market_selections_by_pair) < stored_market_count:
        warnings.append(
            "live market coverage is lower than the stored snapshot "
            f"({len(snapshot.market_selections_by_pair)} vs {stored_market_count})"
        )
    if not knockout_like_titles:
        warnings.append("no knockout-like events were returned by the current Polymarket pull")

    return {
        "events_seen": snapshot.events_seen,
        "market_count": len(snapshot.market_selections_by_pair),
        "stored_market_count": stored_market_count,
        "market_count_delta_vs_stored": market_count_delta_vs_stored,
        "normalized_group_pair_coverage_count": len(normalized_coverage_pairs),
        "normalized_group_pair_total": len(normalized_schedule_pairs),
        "normalized_group_pair_coverage_ratio": round(
            (
                len(normalized_coverage_pairs) / len(normalized_schedule_pairs)
                if normalized_schedule_pairs
                else 0.0
            ),
            4,
        ),
        "normalized_missing_group_pairs_sample": normalized_missing_pairs[:missing_sample_size],
        "knockout_like_event_count": len(knockout_like_titles),
        "has_knockout_like_events": bool(knockout_like_titles),
        "knockout_like_titles_sample": knockout_like_titles[:missing_sample_size],
        "market_quality": quality,
        "warnings": warnings,
    }


def load_persisted_polymarket_snapshot(path: Path) -> PolymarketSnapshot | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    snapshot = PolymarketSnapshot.from_dict(payload)
    if not snapshot.market_selections_by_pair:
        return None
    return snapshot


def _parse_polymarket_title_to_pair(title: str) -> tuple[str, str] | None:
    if " vs. " not in title:
        return None
    left, right = title.split(" vs. ", 1)
    return left.strip(), right.strip()


def _parse_schedule_matchup_to_pair(matchup: str) -> tuple[str, str] | None:
    if " vs " not in matchup:
        return None
    left, right = matchup.split(" vs ", 1)
    return left.strip(), right.strip()


def _normalize_unordered_pair(pair: tuple[str, str]) -> tuple[str, str]:
    left, right = pair
    return tuple(sorted((left, right)))


def _normalize_canonical_pair(
    pair: tuple[str, str],
    normalizer: TeamNameNormalizer,
) -> tuple[str, str] | None:
    left, right = pair
    left_canonical = normalizer.resolve(left)
    right_canonical = normalizer.resolve(right)
    if left_canonical is None or right_canonical is None:
        return None
    return _normalize_unordered_pair((left_canonical, right_canonical))


def _probe_market_quality(events: list[dict[str, Any]]) -> dict[str, Any]:
    spreads: list[float] = []
    missing_quotes_count = 0
    implied_probability_triples_count = 0
    implied_probability_totals_out_of_band_count = 0
    market_count = 0

    for event in events:
        markets = event.get("markets", [])
        if not isinstance(markets, list):
            continue
        event_mid_probs: list[float] = []

        for market in markets:
            if not isinstance(market, dict):
                continue
            market_count += 1
            bid = _extract_nested_float(market, "bestBidQuote", "value")
            ask = _extract_nested_float(market, "bestAskQuote", "value")
            if bid is None or ask is None:
                missing_quotes_count += 1
                continue
            spreads.append(max(0.0, ask - bid))
            event_mid_probs.append((bid + ask) / 2.0)

        if len(event_mid_probs) == 3:
            implied_probability_triples_count += 1
            implied_total = sum(event_mid_probs)
            if implied_total < 0.90 or implied_total > 1.10:
                implied_probability_totals_out_of_band_count += 1

    average_spread = round(sum(spreads) / len(spreads), 4) if spreads else None
    max_spread = round(max(spreads), 4) if spreads else None
    return {
        "markets_checked": market_count,
        "missing_bid_or_ask_count": missing_quotes_count,
        "average_spread": average_spread,
        "max_spread": max_spread,
        "events_with_3_market_midpoint_totals": implied_probability_triples_count,
        "midpoint_totals_outside_0.90_to_1.10": implied_probability_totals_out_of_band_count,
    }


def _extract_nested_float(payload: dict[str, Any], *path: str) -> float | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    try:
        return float(current)
    except (TypeError, ValueError):
        return None


def _is_knockout_like_event_title(title: str) -> bool:
    lowered = title.lower()
    return any(
        keyword in lowered
        for keyword in (
            "round of 32",
            "round of 16",
            "quarterfinal",
            "semifinal",
            "final",
            "winner match",
        )
    )
