# Phase D - Polymarket Signal Integration

Status: In Progress

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
   - [x] fetch odds for relevant teams/markets
   - [x] map market symbols/contracts to project team names
   - [x] parse timestamp/freshness metadata
2. Implement reliability gates:
   - [x] minimum liquidity threshold
   - [x] maximum spread threshold
   - [x] data freshness TTL
4. Implement `hybrid` provider:
   - [x] market-first when confidence gates pass
   - [x] fallback to FIFA ranking when stale/missing
   - [ ] optional weighted blend if both are valid
5. Add runtime configuration flags:
   - [x] strength mode (`fifa`, `market`, `hybrid`)
   - [x] manual refresh status / cache TTL

Removed from immediate scope for this patch:
3. Implement `market` strength provider:
   - deferred; current production path is `hybrid` with deterministic rating fallback

## Implementation Snapshot (2026-06-02)

Implemented in this repo:
- `src/polymarket.py`
  - `GatewayPolymarketSnapshotFetcher`
  - `PolymarketSnapshotStore`
  - `HybridPairwiseWinModel`
- `src/engine.py`
  - default world-ranking path now constructs `HybridPairwiseWinModel`
  - added explicit `refresh_polymarket_snapshot()` helper
- `tests/test_polymarket.py`
  - market-hit behavior
  - stale/missing fallback behavior
  - last-known-good snapshot persistence behavior

Design actually shipped:
- Predictor hot path is cache-first and non-blocking with respect to Polymarket.
- Live Polymarket fetch is not required for prediction to succeed.
- If no usable snapshot is present, per-match fallback deterministically delegates to `RatingPairwiseWinModel`.
- The snapshot store persists a last-known-good payload at `data/polymarket_last_snapshot.json`.

Current default behavior:
- `MatchupPredictor.predict(...)` defaults to `market` strength mode and will use Polymarket odds only if a usable cached snapshot already exists.
- FastAPI and Streamlit startup paths perform a best-effort snapshot refresh so each server start tries to use the most recent market signal.
- `MatchupPredictor.refresh_polymarket_snapshot()` can still be called explicitly to fetch a new snapshot.
- Startup refresh failures are logged/reported without blocking later predictions, which protects response latency and avoids UI/API hangs when Polymarket is slow or unstable.
- `fifa`, `hybrid`, and `market` modes are selectable through `PredictorRuntimeConfig`.
- `market` mode visibly reports FIFA fallback usage in prediction metadata when market data is missing or fails gates.
## Tests

Completed:
1. Provider fallback behavior under stale/missing market data.
2. Stable candidate output with deterministic mocked market feed.
3. Last-known-good snapshot survives refresh failure and process restart.

Still useful to add:
4. Fetcher parsing tests against captured Polymarket payload fixtures.
5. Streamlit/browser-level visual regression coverage for explicit snapshot refresh controls.

## Fallback Policy Contract

1. `HybridPairwiseWinModel` should treat market data as usable only when all gates pass:
   - freshness within TTL
   - minimum liquidity met
   - maximum spread not exceeded
2. If any gate fails or market mapping is missing, fallback must deterministically delegate to `RatingPairwiseWinModel`.
3. Fallback should happen at per-match granularity, so mixed coverage across fixtures remains valid.

## Exit Criteria

- [x] Predictor can run using market-driven probabilities with safe fallback behavior.
- [x] UI/API expose an intentional refresh/configuration story for Polymarket snapshots.
- [x] User-selectable strength modes exist (`fifa`, `hybrid`, optional `market`).

## Verification (2026-06-02)

Verified after implementation:
- `pytest tests/test_polymarket.py tests/test_world_ranking.py tests/test_engine.py -q`
- result: `27 passed`

Verified code-path behavior:
- `src/ui.py` creates `MatchupPredictor()` through `get_predictor()`.
- That predictor uses the hybrid-capable simulator factory in `src/engine.py`.
- However, `src/ui.py` does not call `refresh_polymarket_snapshot()`.
- Therefore the UI will only reflect Polymarket odds when a usable cached snapshot already exists on disk or in memory.
- If no snapshot is present, the UI still works, but predictions come from deterministic rating fallback.

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

## Name-Mismatch Diagnosis (2026-06-01)

Live validation snapshot on 2026-06-01:
- Exact-string group-stage coverage remained `47/72` (`65.28%`).
- `25` group-stage fixtures were unmatched under exact string pairing.

Root cause for the `25` unmatched fixtures:
- The unmatched count is driven by team alias differences between local schedule naming and Polymarket naming, not by absent fixtures.
- Extra Polymarket group-stage pairs not found via exact local names: `25` (1:1 with unmatched local pairs).

Observed alias pairs:
- `Turkey` <-> `Turkiye`
- `Iran` <-> `IR Iran`
- `South Korea` <-> `Korea Republic`
- `Czech Republic` <-> `Czechia`
- `Ivory Coast` <-> `Cote d'Ivoire`
- `Curaçao` <-> `Curacao`
- `Cape Verde` <-> `Cabo Verde`
- `DR Congo` <-> `Congo DR`
- `Bosnia and Herzegovina` <-> `Bosnia-Herzegovina`

## Normalization Module Prep

Scaffold added:
- `src/team_name_normalization.py`
  - `normalize_team_name(raw: str) -> str`
  - `TeamNameNormalizer.build(canonical_names, alias_map=None)`
  - `TeamNameNormalizer.resolve(raw_name)`
  - default Polymarket alias table for known World Cup mismatches
- `tests/test_team_name_normalization.py`
  - punctuation/diacritic normalization checks
  - Polymarket alias resolution checks
  - ambiguity guard checks

Validation script enhancement:
- `scripts/validate_polymarket_worldcup_source.py` now reports both:
  - exact-string coverage (`group_pair_coverage_*`)
  - alias-normalized coverage (`normalized_group_pair_coverage_*`)

Normalization outcome achieved on 2026-06-01:
- Exact-string group-stage coverage was `47/72`.
- After alias normalization, group-stage coverage improved to `72/72`.
- This confirmed that the previously unmatched `25` fixtures were caused by naming differences rather than missing Polymarket fixtures.
- The comparison workflow now loads more market pairs for plugin-backed simulation, which materially changes some downstream knockout-path predictions.

## Provider Stability Note (2026-06-02)

Observed behavior during live comparison runs on 2026-06-02:
- Polymarket intermittently returned `HTTP 500` for the `fifawc` events endpoint.
- The failures were not consistent across retries.
- The same comparison commands succeeded when retried sequentially after failing in parallel.
- Successful runs also showed small variation in loaded market-pair counts between runs, which suggests the provider can be transiently unstable or sensitive to concurrent request patterns.

Current interpretation:
- This does not look like a permanent outage.
- This does not currently look like a deterministic bug in local code.
- Treat provider `500` responses as recoverable upstream instability and design ingestion accordingly.

Follow-up for next implementation step:
- expose a deliberate refresh trigger in UI/API or app startup path
- add runtime configuration for mode selection and TTL thresholds
- decide whether a pure `market` mode is still desired, or whether `hybrid` should remain the only market-backed production path
- add payload-fixture tests for fetcher/parser compatibility against real Polymarket responses
