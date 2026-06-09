from __future__ import annotations

import json
import sys
import urllib.request
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from src.engine import MatchupPredictor
    from src.team_name_normalization import TeamNameNormalizer
    from src.world_ranking import (
        FifaTeamPowerModel,
        MatchContext,
        MatchOutcome,
        PairwiseWinModel,
        RatingPairwiseWinModel,
        TeamPowerModel,
        WorldRankingModelConfig,
        WorldRankingTournamentSimulator,
    )
except ModuleNotFoundError:
    from engine import MatchupPredictor
    from team_name_normalization import TeamNameNormalizer
    from world_ranking import (
        FifaTeamPowerModel,
        MatchContext,
        MatchOutcome,
        PairwiseWinModel,
        RatingPairwiseWinModel,
        TeamPowerModel,
        WorldRankingModelConfig,
        WorldRankingTournamentSimulator,
    )

LEAGUE_SLUG = "fwc"
PAGE_SIZE = 20
TARGET_MATCHES = (82, 94)


@dataclass(frozen=True)
class ThreeWayProbability:
    home_win: float
    draw: float
    away_win: float


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; which-matchup/market-plugin-compare)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_event_pair(title: str) -> tuple[str, str] | None:
    if " vs. " not in title:
        return None
    left, right = title.split(" vs. ", 1)
    return left.strip(), right.strip()


def _canonical_pair(
    pair: tuple[str, str],
    normalizer: TeamNameNormalizer,
) -> tuple[str, str] | None:
    left, right = pair
    left_name = normalizer.resolve(left)
    right_name = normalizer.resolve(right)
    if left_name is None or right_name is None:
        return None
    return left_name, right_name


def _to_midpoint_probability(market: dict[str, Any]) -> float | None:
    bid = market.get("bestBidQuote", {}).get("value")
    ask = market.get("bestAskQuote", {}).get("value")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def _event_three_way_probability(event: dict[str, Any]) -> ThreeWayProbability | None:
    markets = event.get("markets", [])
    if not isinstance(markets, list):
        return None

    by_order: dict[int, dict[str, Any]] = {}
    for market in markets:
        if not isinstance(market, dict):
            continue
        if market.get("sportsMarketTypeV2") != "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME":
            continue
        order = market.get("sortOrder")
        if order in (1, 2, 3) and order not in by_order:
            by_order[int(order)] = market

    if set(by_order.keys()) != {1, 2, 3}:
        return None

    p_home = _to_midpoint_probability(by_order[1])
    p_draw = _to_midpoint_probability(by_order[2])
    p_away = _to_midpoint_probability(by_order[3])
    if p_home is None or p_draw is None or p_away is None:
        return None

    total = p_home + p_draw + p_away
    if total <= 0.0:
        return None
    return ThreeWayProbability(
        home_win=p_home / total,
        draw=p_draw / total,
        away_win=p_away / total,
    )


def fetch_world_cup_market_probabilities(
    normalizer: TeamNameNormalizer,
) -> dict[tuple[str, str], ThreeWayProbability]:
    out: dict[tuple[str, str], ThreeWayProbability] = {}
    offset = 0
    while True:
        payload = _fetch_json(
            "https://gateway.polymarket.us/v2/leagues/"
            f"{LEAGUE_SLUG}/events?type=sport&section=general&limit={PAGE_SIZE}&offset={offset}"
        )
        events = payload.get("events", [])
        if not isinstance(events, list) or not events:
            break

        for event in events:
            if not isinstance(event, dict):
                continue
            pair = _parse_event_pair(str(event.get("title", "")))
            if pair is None:
                continue
            canonical_pair = _canonical_pair(pair, normalizer)
            if canonical_pair is None:
                continue
            probs = _event_three_way_probability(event)
            if probs is None:
                continue
            out[canonical_pair] = probs

        offset += PAGE_SIZE
    return out


class PolymarketOddsPluginPairwiseWinModel(PairwiseWinModel):
    """Market-odds plugin with deterministic fallback to rating model."""

    def __init__(
        self,
        probabilities_by_pair: dict[tuple[str, str], ThreeWayProbability],
        fallback: PairwiseWinModel | None = None,
    ) -> None:
        self.probabilities_by_pair = probabilities_by_pair
        self.fallback = fallback or RatingPairwiseWinModel()
        self.group_market_hits = 0
        self.group_fallback_hits = 0
        self.knockout_market_hits = 0
        self.knockout_fallback_hits = 0

    def _lookup(self, home_team: str, away_team: str) -> ThreeWayProbability | None:
        direct = self.probabilities_by_pair.get((home_team, away_team))
        if direct is not None:
            return direct
        reverse = self.probabilities_by_pair.get((away_team, home_team))
        if reverse is None:
            return None
        return ThreeWayProbability(
            home_win=reverse.away_win,
            draw=reverse.draw,
            away_win=reverse.home_win,
        )

    def group_outcomes(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model: TeamPowerModel,
        model_config: WorldRankingModelConfig,
        decisive_band: float,
    ) -> list[MatchOutcome]:
        market_probs = self._lookup(home_team, away_team)
        if market_probs is not None:
            self.group_market_hits += 1
            diff = abs(team_power_model.team_rating(home_team) - team_power_model.team_rating(away_team))
            margin = 2 if diff >= decisive_band else 1
            return [
                MatchOutcome(home_goals=1 + margin, away_goals=1, probability=market_probs.home_win),
                MatchOutcome(home_goals=1, away_goals=1, probability=market_probs.draw),
                MatchOutcome(home_goals=1, away_goals=1 + margin, probability=market_probs.away_win),
            ]

        self.group_fallback_hits += 1
        return self.fallback.group_outcomes(
            home_team,
            away_team,
            match_context,
            team_power_model=team_power_model,
            model_config=model_config,
            decisive_band=decisive_band,
        )

    def knockout_home_win_probability(
        self,
        home_team: str,
        away_team: str,
        match_context: MatchContext,
        *,
        team_power_model: TeamPowerModel,
        model_config: WorldRankingModelConfig,
        draw_band: float,
    ) -> float:
        market_probs = self._lookup(home_team, away_team)
        if market_probs is not None:
            self.knockout_market_hits += 1
            decisive_total = market_probs.home_win + market_probs.away_win
            if decisive_total <= 0.0:
                return 0.5
            return market_probs.home_win / decisive_total

        self.knockout_fallback_hits += 1
        return self.fallback.knockout_home_win_probability(
            home_team,
            away_team,
            match_context,
            team_power_model=team_power_model,
            model_config=model_config,
            draw_band=draw_band,
        )


def _build_simulator(pairwise_model: PairwiseWinModel) -> WorldRankingTournamentSimulator:
    predictor = MatchupPredictor()
    world_cup_data = predictor._load_world_cup_data()
    fifa_ranking_data = predictor._load_fifa_ranking_data()
    team_power_model = FifaTeamPowerModel(
        fifa_ranking_data=fifa_ranking_data,
        default_rank_for_unlisted_team=120,
        default_points_fallback=1400.0,
    )
    return WorldRankingTournamentSimulator(
        world_cup_data=world_cup_data,
        team_power_model=team_power_model,
        pairwise_win_model=pairwise_model,
    )


def _as_map(candidates: list[tuple[str, str, float]]) -> dict[tuple[str, str], float]:
    return {(home, away): prob for home, away, prob in candidates}


def _top_differences(
    baseline: list[tuple[str, str, float]],
    market: list[tuple[str, str, float]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    baseline_map = _as_map(baseline)
    market_map = _as_map(market)
    keys = set(baseline_map.keys()) | set(market_map.keys())
    ranked = sorted(
        keys,
        key=lambda pair: abs(market_map.get(pair, 0.0) - baseline_map.get(pair, 0.0)),
        reverse=True,
    )[:limit]
    out: list[dict[str, Any]] = []
    for home, away in ranked:
        b = baseline_map.get((home, away), 0.0)
        m = market_map.get((home, away), 0.0)
        out.append(
            {
                "pair": f"{home} vs {away}",
                "baseline_probability": round(b, 6),
                "market_plugin_probability": round(m, 6),
                "delta": round(m - b, 6),
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare baseline rating model vs Polymarket market-odds plugin "
            "for selected World Cup match numbers."
        )
    )
    parser.add_argument(
        "--matches",
        type=str,
        default=",".join(str(value) for value in TARGET_MATCHES),
        help="Comma-separated match numbers (example: 82,94).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Top-N candidates to compare per match.",
    )
    args = parser.parse_args()

    selected_matches = tuple(
        int(part.strip()) for part in args.matches.split(",") if part.strip()
    )
    if not selected_matches:
        raise ValueError("No match numbers provided.")

    predictor = MatchupPredictor()
    world_cup_data = predictor._load_world_cup_data()
    canonical_names = [
        str(participant.get("name"))
        for participant in world_cup_data.get("participants", [])
        if isinstance(participant, dict) and participant.get("name")
    ]
    normalizer = TeamNameNormalizer.build(canonical_names=canonical_names)

    market_probs = fetch_world_cup_market_probabilities(normalizer=normalizer)

    baseline_model = RatingPairwiseWinModel()
    market_plugin_model = PolymarketOddsPluginPairwiseWinModel(
        probabilities_by_pair=market_probs,
        fallback=baseline_model,
    )

    baseline_sim = _build_simulator(baseline_model)
    market_sim = _build_simulator(market_plugin_model)

    report: dict[str, Any] = {
        "market_source": "Polymarket Sports API",
        "league_slug": LEAGUE_SLUG,
        "market_probability_pairs_loaded": len(market_probs),
        "selected_matches": list(selected_matches),
        "top_n_limit": args.limit,
        "matches": {},
    }

    for match_number in selected_matches:
        baseline = baseline_sim.predict_matchup_candidates(match_number=match_number, limit=args.limit)
        market = market_sim.predict_matchup_candidates(match_number=match_number, limit=args.limit)
        report["matches"][str(match_number)] = {
            "baseline_top_n": [
                {"pair": f"{h} vs {a}", "probability": round(p, 6)} for h, a, p in baseline
            ],
            "market_plugin_top_n": [
                {"pair": f"{h} vs {a}", "probability": round(p, 6)} for h, a, p in market
            ],
            "largest_probability_deltas": _top_differences(baseline, market, limit=8),
        }

    report["plugin_usage"] = {
        "group_market_hits": market_plugin_model.group_market_hits,
        "group_fallback_hits": market_plugin_model.group_fallback_hits,
        "knockout_market_hits": market_plugin_model.knockout_market_hits,
        "knockout_fallback_hits": market_plugin_model.knockout_fallback_hits,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
