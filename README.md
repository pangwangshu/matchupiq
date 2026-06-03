# Which Matchup

Predict the most likely FIFA World Cup 2026 matchups for a selected scheduled match.

The project combines tournament-bracket rules, group-stage scenario search, FIFA rankings, and optional Polymarket market data to estimate which teams are most likely to meet in later knockout rounds.

## What It Does

- Accepts a target World Cup match as input, usually by scheduled `match_number`.
- Returns the most likely home/away matchup candidates for that match.
- Uses real completed match results when they are available in `data/matches_2026.json`.
- Serves predictions through both:
  - a FastAPI API
  - a Streamlit UI

## Current Runtime Behavior

The current default runtime path is:

- `FifaTeamPowerModel` for team strength
- `HybridPairwiseWinModel` for match probabilities
- deterministic fallback to `RatingPairwiseWinModel` whenever market data is missing, stale, or low quality

That means the app is resilient even if Polymarket data is unavailable.

## Quick Start

1. Create and activate a virtual environment.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies.

```powershell
pip install -r requirements.txt
```

3. Run the API.

```powershell
uvicorn src.api:app --reload
```

4. Run the UI.

```powershell
streamlit run src/ui.py
```

## API

### Health Check

`GET /health`

Example response:

```json
{
  "status": "ok"
}
```

### Prediction Endpoint

`POST /predict`

Request body:

```json
{
  "match_id": "82"
}
```

Example response:

```json
{
  "match_id": "82",
  "status": "predicted",
  "confirmed_matchup": null,
  "top_candidates": [
    {
      "home_team": "Germany",
      "away_team": "Mexico",
      "score": 0.2145,
      "reason": "Predicted via scenario-search simulation (group standings + knockout outcome branching)."
    }
  ]
}
```

Notes:

- `match_id` should match a key understood by the app. In practice, the prediction engine is designed around scheduled World Cup `match_number` values such as `"74"` or `"82"`.
- The API currently returns `status="predicted"` for prediction responses.
- Group-stage matches are fixed in the UI and do not require probabilistic prediction.

## UI

The Streamlit app provides:

- city and round/category filters
- a match picker for the 2026 tournament schedule
- fixed rendering for group-stage matches
- top matchup candidates for unresolved knockout matches

The UI entry point is [src/ui.py](/d:/src/which_matchup/src/ui.py).

## How Predictions Work

The prediction engine combines tournament rules with probabilistic simulation.

1. Resolve the valid rule space.
   Only teams that can legally reach the selected match are considered.

2. Collapse uncertainty with completed results.
   If prerequisite matches have already been played and recorded, the candidate space narrows immediately.

3. Simulate group-stage outcomes.
   The engine uses beam search to explore likely group tables without exploding combinatorially.

4. Propagate knockout uncertainty.
   Winner and loser distributions flow through the bracket for unresolved matches.

5. Aggregate matchup probabilities.
   The final output is a ranked list of likely home/away pairings.

## Main Components

- `src/api.py`
  FastAPI app with `/health` and `/predict`.
- `src/ui.py`
  Streamlit frontend.
- `src/engine.py`
  High-level prediction entry point and simulator wiring.
- `src/tournament.py`
  Tournament rule resolution, group-slot parsing, and valid-pair generation.
- `src/world_ranking.py`
  Scenario-search simulator and rating-based models.
- `src/polymarket.py`
  Market snapshot fetching, caching, and hybrid pairwise model.
- `src/prediction_cache.py`
  Stale-while-refresh prediction caching layer.
- `src/models.py`
  Request and response models.

## Data Files

### `data/worldcup_2026_static.json`

Canonical tournament structure.

Expected to contain:

- `participants`
- `groups`
- `schedule`

### `data/fifa_men_ranking_static.json`

Static ranking snapshot used by `FifaTeamPowerModel`.

Expected to contain:

- `participants_rankings`

### `data/matches_2026.json`

UI match list plus live/completed-result overrides.

Fields currently used by the app include:

- `match_number`, or a parseable `label` containing `Match <n>`
- `status` or `played`
- `confirmed_home`, `confirmed_away`
- fallback team fields such as `home_team`, `away_team`, `home`, `away`
- score fields such as `home_goals`, `away_goals`, with `home_score`, `away_score` as fallback

## Caching

Predictions are served through `PredictionCacheService`.

Current behavior:

- LRU cache with TTL
- stale-while-refresh reads
- per-match single-flight behavior on cold cache misses
- background refresh worker pool
- retry cooldown after refresh failures

Polymarket snapshots are cached separately in `PolymarketSnapshotStore`, including disk persistence of the last known good snapshot.

## Development

Run tests:

```powershell
pytest -q tests
```

Run lint:

```powershell
.\.venv\Scripts\ruff.exe check src tests
```

## CI

GitHub Actions runs:

- Ruff lint checks
- unit tests with `pytest`

Workflow file:

- [`.github/workflows/ci.yml`](/d:/src/which_matchup/.github/workflows/ci.yml)

## Limitations

- Predictions depend on the quality of the static tournament and ranking snapshots.
- Market-backed predictions fall back to rating-based estimates when market data is unavailable or unreliable.
- The API model supports `confirmed_matchup`, but the current engine path primarily returns ranked predicted candidates.
