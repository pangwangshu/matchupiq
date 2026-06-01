# Phase D - Polymarket Signal Integration

Status: Planned

## Goal

Add a realtime or semi-realtime market signal provider and allow selecting FIFA, market-only, or hybrid strength modes.

## Scope

New modules expected:
- market data provider (Polymarket fetch + normalization)
- strength provider implementation using market odds

Integration points:
- `src/engine.py` (mode selection/factory wiring)
- generalized simulator from Phase C

## Tasks

1. Build Polymarket ingestion component:
   - fetch odds for relevant teams/markets
   - map market symbols/contracts to project team names
   - parse timestamp/freshness metadata
2. Implement reliability gates:
   - minimum liquidity threshold
   - maximum spread threshold
   - data freshness TTL
3. Implement `market` strength provider:
   - implied probability conversion
   - team-level strength mapping where needed
4. Add `hybrid` provider:
   - market-first when confidence gates pass
   - fallback to FIFA ranking when stale/missing
   - optional weighted blend if both are valid
5. Add runtime configuration flags:
   - strength mode (`fifa`, `market`, `hybrid`)
   - refresh interval / cache TTL

## Tests

1. Provider fallback behavior under stale/missing market data.
2. Mapping and normalization correctness for sample odds payload.
3. Stable candidate output with deterministic mocked market feed.

## Fallback Policy Contract

1. `HybridPairwiseWinModel` should treat market data as usable only when all gates pass:
   - freshness within TTL
   - minimum liquidity met
   - maximum spread not exceeded
2. If any gate fails or market mapping is missing, fallback must deterministically delegate to `RatingPairwiseWinModel`.
3. Fallback should happen at per-match granularity, so mixed coverage across fixtures remains valid.

## Exit Criteria

- Predictor can run using market-driven probabilities with safe fallback behavior.

## Data Source Validation (2026-05-31)

Selected source: `Polymarket Sports API` via `https://gateway.polymarket.us`

Why this source:
- Public endpoint access (no API key required for read paths tested)
- World Cup league slug available (`fifawc`)
- Match-level event payload includes teams, market sides, bid/ask quotes, and freshness timestamps
- Supports reliability gates already planned here (spread, freshness, and liquidity/market-depth proxies)

Validation script:
- `python scripts/validate_polymarket_worldcup_source.py`

Observed from live probe on 2026-05-31:
- Local schedule counts: `72` group-stage, `32` knockout-stage
- Polymarket `fifawc` match events currently returned: `72`
- Group pair coverage vs local static schedule by team-name matching: `47/72` (`65.28%`)
- Knockout-like match events currently detected in feed: `0`
- Market quality sample (216 markets across 72 events):
  - Missing bid/ask quote on `27` markets
  - Average spread `0.1133`, max spread `0.42`
  - 3-way midpoint implied-probability totals out of `[0.90, 1.10]`: `3` events

Implications for Phase D:
- Use Polymarket as primary source for live odds ingestion.
- Keep deterministic fallback to `RatingPairwiseWinModel` as required by the fallback contract.
- Implement explicit team-name normalization/alias mapping (static schedule names and market team names are not always aligned).
- Treat knockout coverage as dynamic: markets may appear later as bracket participants become known.
