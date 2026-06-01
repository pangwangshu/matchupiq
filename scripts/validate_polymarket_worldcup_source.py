from __future__ import annotations

import json
import statistics
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.team_name_normalization import TeamNameNormalizer

WORLD_CUP_PATH = Path(__file__).resolve().parent.parent / "data" / "worldcup_2026_static.json"
POLYMARKET_WORLD_CUP_LEAGUE = "fifawc"
PAGE_SIZE = 20
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; which-matchup/phase-d-source-validation)",
    "Accept": "application/json",
}


@dataclass(frozen=True)
class OddsProbeSummary:
    market_count: int
    missing_quotes_count: int
    average_spread: float | None
    max_spread: float | None
    implied_probability_triples_count: int
    implied_probability_totals_out_of_band_count: int


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_world_cup_schedule() -> list[dict]:
    payload = json.loads(WORLD_CUP_PATH.read_text(encoding="utf-8"))
    schedule = payload.get("schedule", [])
    if not isinstance(schedule, list):
        raise ValueError("worldcup_2026_static.json schedule must be a list.")
    return schedule


def _load_world_cup_payload() -> dict:
    payload = json.loads(WORLD_CUP_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("worldcup_2026_static.json must contain a JSON object.")
    return payload


def _fetch_world_cup_events() -> list[dict]:
    events: list[dict] = []
    offset = 0
    while True:
        url = (
            "https://gateway.polymarket.us/v2/leagues/"
            f"{POLYMARKET_WORLD_CUP_LEAGUE}/events?type=sport&section=general"
            f"&limit={PAGE_SIZE}&offset={offset}"
        )
        payload = _get_json(url)
        batch = payload.get("events", [])
        if not isinstance(batch, list) or not batch:
            break
        events.extend(batch)
        offset += PAGE_SIZE
    return events


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


def _probe_market_quality(events: list[dict]) -> OddsProbeSummary:
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
            bid = market.get("bestBidQuote", {}).get("value")
            ask = market.get("bestAskQuote", {}).get("value")
            if bid is None or ask is None:
                missing_quotes_count += 1
                continue
            bid_value = float(bid)
            ask_value = float(ask)
            spreads.append(max(0.0, ask_value - bid_value))
            event_mid_probs.append((bid_value + ask_value) / 2.0)

        if len(event_mid_probs) == 3:
            implied_probability_triples_count += 1
            implied_total = sum(event_mid_probs)
            if implied_total < 0.90 or implied_total > 1.10:
                implied_probability_totals_out_of_band_count += 1

    average_spread = statistics.mean(spreads) if spreads else None
    max_spread = max(spreads) if spreads else None
    return OddsProbeSummary(
        market_count=market_count,
        missing_quotes_count=missing_quotes_count,
        average_spread=average_spread,
        max_spread=max_spread,
        implied_probability_triples_count=implied_probability_triples_count,
        implied_probability_totals_out_of_band_count=implied_probability_totals_out_of_band_count,
    )


def _knockout_like_events(events: list[dict]) -> list[dict]:
    keywords = (
        "round of 32",
        "round of 16",
        "quarterfinal",
        "semifinal",
        "final",
        "winner match",
    )
    out: list[dict] = []
    for event in events:
        title = str(event.get("title", ""))
        lowered = title.lower()
        if any(keyword in lowered for keyword in keywords):
            out.append(event)
    return out


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    try:
        world_cup_payload = _load_world_cup_payload()
        schedule = world_cup_payload.get("schedule", [])
        if not isinstance(schedule, list):
            raise ValueError("worldcup_2026_static.json schedule must be a list.")
        events = _fetch_world_cup_events()
    except urllib.error.HTTPError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"HTTPError {exc.code}",
                    "message": str(exc),
                    "fetched_at_utc": _iso_utc_now(),
                },
                indent=2,
            )
        )
        return 1
    except Exception as exc:  # pragma: no cover - defensive script path
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "fetched_at_utc": _iso_utc_now(),
                },
                indent=2,
            )
        )
        return 1

    group_matches = [match for match in schedule if match.get("stage") == "group_stage"]
    knockout_matches = [match for match in schedule if match.get("stage") != "group_stage"]
    canonical_names = [
        str(participant.get("name"))
        for participant in world_cup_payload.get("participants", [])
        if isinstance(participant, dict) and participant.get("name")
    ]
    normalizer = TeamNameNormalizer.build(canonical_names=canonical_names)

    schedule_group_pairs = {
        _normalize_unordered_pair(pair)
        for match in group_matches
        for pair in [_parse_schedule_matchup_to_pair(str(match.get("matchup", "")))]
        if pair is not None
    }
    event_pairs = {
        _normalize_unordered_pair(pair)
        for event in events
        for pair in [_parse_polymarket_title_to_pair(str(event.get("title", "")))]
        if pair is not None
    }

    coverage_pairs = schedule_group_pairs & event_pairs
    missing_pairs = sorted(schedule_group_pairs - event_pairs)

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

    quality = _probe_market_quality(events)
    knockout_candidates = _knockout_like_events(events)

    output = {
        "ok": True,
        "data_source": "Polymarket Sports API",
        "league_slug": POLYMARKET_WORLD_CUP_LEAGUE,
        "fetched_at_utc": _iso_utc_now(),
        "schedule_group_match_count": len(group_matches),
        "schedule_knockout_match_count": len(knockout_matches),
        "polymarket_event_count": len(events),
        "group_pair_coverage_count": len(coverage_pairs),
        "group_pair_total": len(schedule_group_pairs),
        "group_pair_coverage_ratio": round(
            (len(coverage_pairs) / len(schedule_group_pairs)) if schedule_group_pairs else 0.0, 4
        ),
        "missing_group_pairs_sample": missing_pairs[:8],
        "normalized_group_pair_coverage_count": len(normalized_coverage_pairs),
        "normalized_group_pair_total": len(normalized_schedule_pairs),
        "normalized_group_pair_coverage_ratio": round(
            (len(normalized_coverage_pairs) / len(normalized_schedule_pairs)) if normalized_schedule_pairs else 0.0,
            4,
        ),
        "normalized_missing_group_pairs_sample": normalized_missing_pairs[:8],
        "knockout_like_event_count": len(knockout_candidates),
        "first_event_title": events[0].get("title") if events else None,
        "last_event_title": events[-1].get("title") if events else None,
        "market_quality": {
            "markets_checked": quality.market_count,
            "missing_bid_or_ask_count": quality.missing_quotes_count,
            "average_spread": round(quality.average_spread, 4) if quality.average_spread is not None else None,
            "max_spread": round(quality.max_spread, 4) if quality.max_spread is not None else None,
            "events_with_3_market_midpoint_totals": quality.implied_probability_triples_count,
            "midpoint_totals_outside_0.90_to_1.10": quality.implied_probability_totals_out_of_band_count,
        },
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
