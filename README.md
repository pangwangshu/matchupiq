# Which Matchup

Predict likely FIFA World Cup 2026 knockout matchups for a selected match.

## What this project does
- Input: a 2026 World Cup match (example: Round of 32 Match 82)
- Output:
  - If already determined/played: returns confirmed teams
  - Otherwise: returns top 10 most likely matchups ranked by score

## Quick start

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Run API (optional):

```powershell
uvicorn src.api:app --reload
```

4. Run UI:

```powershell
streamlit run src/ui.py
```

## API usage

POST `http://127.0.0.1:8000/predict`

```json
{
  "match_id": "400021525"
}
```

## High-level architecture

```text
Client (UI/API caller)
        |
        v
FastAPI / Streamlit
        |
        v
PredictionCacheService
        |
        v
MatchupPredictor
        |
        v
WorldRankingTournamentSimulator
   |                         |
   v                         v
TeamPowerModel          PairwiseWinModel
 (team strength)        (match win probabilities)
```

## Prediction methodology

The prediction engine combines tournament structure constraints with probabilistic scenario search.

1. Resolve valid teams by tournament rules:
- Uses bracket/group slot logic (`Winner Match X`, `Group A winners`, etc.) to build only valid candidate pairs.
- If prerequisite matches are completed, real results are used directly to collapse uncertainty.

2. Simulate group-stage scenario space:
- Uses beam search over group outcomes to keep the most likely world states while controlling combinatorial growth.
- Group standings are recomputed per scenario with deterministic tie-breaking.

3. Propagate knockout uncertainty:
- For unresolved knockout matches, winner/loser distributions are propagated through the bracket.
- For resolved matches, outcomes are fixed from live result state.

4. Score and rank matchup candidates:
- Aggregates matchup probabilities across world scenarios.
- Returns top candidates sorted by probability-like score.

### Strength and probability models

- `TeamPowerModel`: static team-level strength source (current default: FIFA snapshot).
- `PairwiseWinModel`: match-level win probability source (current default: rating-based model).
- `MatchContext`: each pairwise call gets context (`match_number`, `stage`, `date`, `group`) to support future market-backed models.

## Model modes

| Mode | Team power source | Pairwise source | Status |
| --- | --- | --- | --- |
| `fifa` | `FifaTeamPowerModel` | `RatingPairwiseWinModel` | Implemented (default) |
| `market` | `FifaTeamPowerModel` (or future market-derived power) | Market odds only | Planned (Phase D) |
| `hybrid` | `FifaTeamPowerModel` | Market-first with deterministic fallback to `RatingPairwiseWinModel` | Planned (Phase D) |

Current runtime wiring uses the `fifa` path by default through `WorldRankingSimulatorFactory`.

## Caching strategy

Predictions are served through `PredictionCacheService` (`src/prediction_cache.py`) to reduce repeated recomputation.

- `LRU + TTL`: cache entries are bounded by `maxsize` and expire by `ttl_seconds` (default 15 minutes).
- `Stale-while-refresh`: expired entries are returned immediately, and a background refresh is scheduled.
- `Single-flight on misses`: per-match locks prevent duplicate concurrent cold computations.
- `Refresh cooldown`: failed/stale refresh retries are rate-limited (`refresh_retry_cooldown_seconds`, default 30s).
- `Background workers`: refresh jobs run in a small thread pool (`max_refresh_workers`, default 2).
- `Failure handling`: refresh errors keep prior value and are logged; serving does not hard-fail if stale value exists.

## Data contracts

### `data/worldcup_2026_static.json`

- Used for canonical tournament structure.
- Expected keys:
  - `groups`
  - `schedule` (with `match_number`, `stage`, `matchup`/`comment`, and optional `group`, `date` used for `MatchContext`)
  - `participants`

### `data/fifa_men_ranking_static.json`

- Used by `FifaTeamPowerModel`.
- Expected shape:
  - `participants_rankings`: list of rows containing:
    - `participant_name`
    - `points`
    - `rank`

### `data/matches_2026.json`

- Used for live/completed result overrides and input listing.
- For live-result collapse, resolver accepts:
  - Match identity:
    - `match_number` (or parseable `label` containing `Match <n>`)
  - Status:
    - `played` boolean, or `status` in {`played`, `completed`, `complete`, `final`, `finished`, `ft`}
  - Teams:
    - preferred: `confirmed_home`, `confirmed_away`
    - fallback: `home_team`/`away_team` or `home`/`away`
  - Score:
    - preferred: `home_goals`, `away_goals`
    - fallback: `home_score`, `away_score`

## Notes
- Current signal providers are scaffolded with deterministic example values.
- Replace `SignalProvider` implementations in `src/signals.py` with live data sources.
- Match metadata is stored in `data/matches_2026.json`.
- World-ranking model tuning parameters live in `data/world_ranking_model_config.json`.
