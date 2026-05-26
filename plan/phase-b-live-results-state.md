# Phase B - Live Result Aware Tournament State

Status: Planned

## Goal

Make tournament resolution use real match results for completed matches while preserving probabilistic branching for unresolved matches.

## Scope

Primary target: `src/tournament.py`  
Secondary integration: `src/engine.py`

## Tasks

1. Define a match-result state model:
   - `played` flag
   - `home_team`, `away_team`
   - `home_goals`, `away_goals`
   - winner/loser derivation
2. Extend resolver APIs to accept state:
   - `resolve_slot_teams(slot_text, state, ...)`
   - `resolve_match_team_pool(match_number, state, ...)`
   - `build_rule_based_pairs(match_number, state)`
3. For `Winner Match X` / `Loser Match X`:
   - If match X has real result, return resolved team directly.
   - If not, fall back to candidate pool logic.
4. Add group table computation from real group-stage scores:
   - points, goal difference, goals for
   - deterministic tiebreak ordering for stable output
5. Wire engine to pass result state into resolver path.

## Data Contract Work

Add/consume a results feed (file or API response) keyed by `match_number` with score and status fields.

## Tests

1. Resolver returns concrete teams for winner/loser slots when prior result exists.
2. Resolver keeps current behavior when no results are available.
3. Group-slot outputs (winners/runners-up/third place) collapse correctly as groups complete.
4. Engine still returns valid top candidates list for unresolved knockout fixtures.

## Exit Criteria

- Completed matches no longer branch in scenario generation.
- Candidate space narrows automatically as real results arrive.
