# Phase E - Validation Rollout and Monitoring

Status: Planned

## Goal

Ship refactor safely with regression protection, staged rollout controls, and operational visibility.

## Scope

API surface:
- `src/api.py`

Prediction stack:
- `src/engine.py`
- `src/tournament.py`
- simulator/strength modules

Testing + scripts:
- `tests/*`
- `scripts/*` (ingestion/refresh jobs)

## Tasks

1. Add schema validators for external payloads:
   - live match results schema
   - Polymarket payload schema
2. Add end-to-end smoke tests for `/predict` in each strength mode.
3. Add regression snapshots for selected match IDs across stages.
4. Add logging/metrics for:
   - selected strength mode
   - data staleness
   - fallback reasons
   - prediction latency
5. Rollout strategy:
   - start with `fifa` default
   - enable `hybrid` in staging
   - promote gradually after stability checks

## Acceptance Criteria

1. No API contract regression in `PredictionResponse`.
2. All tests pass in CI.
3. Fallback paths are observable and auditable.
4. Prediction quality and latency remain within agreed bounds.

## Operational Notes

- Keep deterministic mode available for debugging and offline runs.
- Preserve static-file fallback so the app still works during upstream outages.
