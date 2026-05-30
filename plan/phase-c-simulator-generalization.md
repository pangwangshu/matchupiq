# Phase C - Generalize World Ranking Simulator

Status: In Progress

## Goal

Refactor `src/world_ranking.py` from FIFA-specific implementation into a reusable scenario engine that can consume different strength sources.

## Scope

Primary target: `src/world_ranking.py`  
Supporting updates: `src/engine.py`, tests

## Tasks

1. Introduce a strength abstraction:
   - team rating lookup
   - pairwise win probability calculation
2. Replace direct FIFA ranking calls in simulator internals with abstraction calls.
3. Keep beam-search world scenario logic unchanged where possible.
4. Ensure live-result constraints can be applied:
   - fixed outcomes for completed matches
   - branching only for unresolved matches
5. Preserve backward-compatible outputs:
   - `predict_matchup_candidates`
   - `simulate_tournament` helper

## Tests

1. Deterministic outputs for fixed input strength provider.
2. Rule-space validity for predicted pairs.
3. Probability normalization remains within expected range.
4. Existing tests migrate with minimal fixture changes.

## Exit Criteria

- Simulator no longer hard-depends on FIFA ranking schema.
- Strength source can be swapped via factory/provider wiring.

## Delivered (Current Slice)

1. Added a `StrengthProvider` abstraction and default `FifaRankingStrengthProvider`.
2. Updated `WorldRankingTournamentSimulator` to accept an injected strength provider while keeping FIFA-backed defaults.
3. Wired `WorldRankingSimulatorFactory` to build and pass the default FIFA strength provider.
4. Added deterministic regression coverage for custom provider injection.
