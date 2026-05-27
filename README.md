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

## Notes
- Current signal providers are scaffolded with deterministic example values.
- Replace `SignalProvider` implementations in `src/signals.py` with live data sources.
- Match metadata is stored in `data/matches_2026.json`.
- World-ranking model tuning parameters live in `data/world_ranking_model_config.json`.
