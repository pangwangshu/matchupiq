# Phase C - Generalize World Ranking Simulator

Status: Completed

## Goal

Refactor `src/world_ranking.py` from FIFA-specific implementation into a reusable scenario engine with:
- static team power model (e.g. FIFA snapshot)
- dynamic pairwise win model (e.g. prediction-market odds + fallback)

## Scope

Primary target: `src/world_ranking.py`  
Supporting updates: `src/engine.py`, tests

## Tasks

1. Introduce a split abstraction:
   - `TeamPowerModel` for static team strength
   - `PairwiseWinModel` for dynamic match-level win probabilities
2. Replace direct FIFA ranking calls and embedded pairwise math with abstraction calls.
3. Keep beam-search world scenario logic unchanged where possible.
4. Ensure live-result constraints can be applied:
   - fixed outcomes for completed matches
   - branching only for unresolved matches
5. Preserve backward-compatible outputs:
   - `predict_matchup_candidates`
   - `simulate_tournament` helper

## Tests

1. Deterministic outputs for fixed `TeamPowerModel` + fixed `PairwiseWinModel`.
2. Rule-space validity for predicted pairs.
3. Probability normalization remains within expected range.
4. Existing tests migrate with minimal fixture changes.

## Exit Criteria

- Simulator no longer hard-depends on FIFA ranking schema.
- Team power source can be swapped independently from pairwise win source.
- Pairwise win model can be backed by market odds with deterministic fallback.

## Delivered (Current Slice)

1. Added split abstractions:
   - `TeamPowerModel`
   - `PairwiseWinModel`
2. Added defaults:
   - `FifaTeamPowerModel` for static team strength
   - `RatingPairwiseWinModel` for rating-derived pairwise probabilities
3. Updated `WorldRankingTournamentSimulator` to depend on `team_power_model` and `pairwise_win_model`.
4. Updated `TournamentSimulatorFactory` wiring to build team-power and pairwise models separately.
5. Added deterministic regression coverage for simulator construction without FIFA payload semantics (custom team-power + custom pairwise model injection).
6. Added `MatchContext` plumbing to pairwise model calls (match number/stage/date/group) to support fixture-aware probability sources.
7. Added strict pairwise probability contracts:
   - group outcomes must be finite, non-negative, and normalized
   - knockout probability must be finite and is clamped to `[0, 1]` as a simulator safety guard
8. Updated predictor reason text to model-neutral wording and expanded regression checks for context propagation and probability-contract enforcement.
