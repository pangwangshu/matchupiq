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

## Exit Criteria

- Predictor can run using market-driven probabilities with safe fallback behavior.
