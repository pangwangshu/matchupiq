# Phase A - Dependency Injection Foundations

Status: Completed

## Goal

Introduce clean module boundaries in `src/engine.py` without changing runtime behavior.

## Why

Before adding live scores and market signals, the predictor needs injectable data and simulation dependencies.

## Delivered Changes

1. Added `MatchDataProvider` protocol.
2. Added `TournamentSimulator` protocol.
3. Added `TournamentSimulatorFactory` protocol.
4. Added `StaticJsonMatchDataProvider` default implementation.
5. Added `WorldRankingSimulatorFactory` default implementation.
6. Updated `MatchupPredictor` constructor to accept optional injected dependencies.
7. Preserved current output contract and behavior.

## Files Touched

- `src/engine.py`

## Verification

- Full test suite passed after refactor.
- Existing API and UI call paths remained unchanged.

## Exit Criteria

- Predictor can be wired with alternative providers/factories without touching core orchestration logic.
