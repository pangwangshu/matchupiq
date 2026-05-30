# Refactor Plan: World Cup Live Data + Market Signal

This folder tracks the refactor roadmap to:

1. Replace simulated match outcomes with real World Cup results once matches start.
2. Add a realtime/semi-realtime market signal (Polymarket) to complement or replace FIFA ranking signal.

The World Cup 2026 schedule in this project starts on **June 11, 2026** (`match_number=1`).

## Phases

1. [Phase A - Dependency Injection Foundations](./phase-a-dependency-injection.md) (Completed)
2. [Phase B - Live Result Aware Tournament State](./phase-b-live-results-state.md) (Completed)
3. [Phase C - Generalize World Ranking Simulator](./phase-c-simulator-generalization.md) (Completed)
4. [Phase D - Polymarket Signal Integration](./phase-d-polymarket-integration.md)
5. [Phase E - Validation Rollout and Monitoring](./phase-e-rollout-validation.md)
6. [Phase F - Community Feedback Hardening](./phase-f-community-feedback-hardening.md)

## Current Status

- Phase A completed in `src/engine.py` with behavior preserved and tests passing.
- Phase B completed with live-result-aware resolver and scenario narrowing behavior.
- Phase C completed with split abstraction:
  - static `TeamPowerModel`
  - dynamic `PairwiseWinModel` with `MatchContext`
  - probability contract checks + deterministic fallback contract notes
- Next recommended step: Phase D.
- Community feedback capture for future reference: `feedback/reddit-feedback-2026-05-27.md`.
