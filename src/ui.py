from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

try:
    from src.engine import MatchupPredictor
except ModuleNotFoundError:
    from engine import MatchupPredictor

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "matches_2026.json"


@st.cache_resource
def get_predictor() -> MatchupPredictor:
    return MatchupPredictor()


def load_match_options() -> dict[str, str]:
    with DATA_PATH.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    return {
        match_id: f"{m['label']} ({match_id})"
        for match_id, m in payload.items()
    }


def main() -> None:
    st.set_page_config(page_title="Which Matchup", page_icon="⚽", layout="centered")
    st.title("Which Matchup")
    st.caption("Predict the most likely 2026 World Cup knockout matchup for a selected match.")

    options = load_match_options()
    selected = st.selectbox(
        "Select a match",
        options=list(options.keys()),
        format_func=lambda x: options[x],
    )

    if st.button("Predict matchup", type="primary"):
        predictor = get_predictor()
        result = predictor.predict(selected)

        if result.status == "confirmed" and result.confirmed_matchup:
            st.success("Matchup already confirmed")
            st.write(
                f"**{result.confirmed_matchup.home_team} vs {result.confirmed_matchup.away_team}**"
            )
        else:
            st.info("Top 10 predicted matchups")
            rows = [
                {
                    "Rank": i,
                    "Matchup": f"{candidate.home_team} vs {candidate.away_team}",
                    "Score": candidate.score,
                    "Signals": candidate.reason,
                }
                for i, candidate in enumerate(result.top_candidates, start=1)
            ]
            st.dataframe(rows, hide_index=True, use_container_width=True)


if __name__ == "__main__":
    main()
