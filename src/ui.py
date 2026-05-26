from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

import streamlit as st

try:
    from src.engine import MatchupPredictor
except ModuleNotFoundError:
    from engine import MatchupPredictor

MATCHUP_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "matches_2026.json"
WORLD_CUP_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "worldcup_2026_static.json"


@st.cache_resource
def get_predictor() -> MatchupPredictor:
    return MatchupPredictor()


def load_match_options() -> dict[str, str]:
    with MATCHUP_DATA_PATH.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    return {
        match_id: f"{m['label']} ({match_id})"
        for match_id, m in payload.items()
    }


@st.cache_data
def load_world_cup_data() -> dict:
    with WORLD_CUP_DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def to_short_date(date_text: str) -> str:
    try:
        parsed = datetime.strptime(date_text, "%A %d %B %Y")
        return parsed.strftime("%d %b %Y")
    except (TypeError, ValueError):
        return date_text


def get_round_label(match: dict) -> str:
    group = match.get("group")
    if group:
        return group.replace("Group", "GROUP")

    stage_labels = {
        "round_of_32": "Round of 32",
        "round_of_16": "Round of 16",
        "quarter_finals": "Quarter-Final",
        "semi_finals": "Semi-Final",
    }
    stage = match.get("stage")
    if stage in stage_labels:
        return stage_labels[stage]

    if stage == "final_and_third_place":
        matchup = str(match.get("matchup", "")).lower()
        if matchup.startswith("loser"):
            return "3rd Place"
        return "Final"

    return str(stage).replace("_", " ").title()


def render_matchup_predictor() -> None:
    world_cup = load_world_cup_data()
    schedule = world_cup.get("schedule", [])

    options = load_match_options()
    option_match_ids = set(options.keys())
    all_match_options = {
        str(m["match_number"]): (
            f"Match {m['match_number']} - {to_short_date(m['date'])} - {m['city']} - "
            f"{get_round_label(m)} - "
            f"{m['matchup']}"
        )
        for m in schedule
    }
    placeholder = "-- SELECT A WORLD CUP 2026 MATCH --"
    selected = st.selectbox(
        "Pick a FIFA World Cup 2026 match to predict",
        options=[placeholder] + list(all_match_options.keys()),
        format_func=lambda x: all_match_options[x] if x != placeholder else placeholder,
    )

    if selected == placeholder:
        return

    selected_match = next(
        (m for m in schedule if str(m["match_number"]) == selected),
        None,
    )
    if selected_match and selected_match.get("stage") == "group_stage":
        st.success("Group stage matchup is fixed")
        st.write(f"**{selected_match['matchup']}**")
        st.write(
            f"{selected_match['date']} {selected_match['local_time']} | {selected_match['stadium']}, {selected_match['city']}"
        )
        if selected_match["comment"]:
            st.write(f"Rule: {selected_match['comment']}")
        return

    if selected not in option_match_ids:
        st.info(
            "Prediction model data is not configured for this match yet. Showing actual schedule data."
        )
        if selected_match:
            st.write(f"**{selected_match['matchup']}**")
            st.write(
                f"{selected_match['date']} {selected_match['local_time']} | {selected_match['stadium']}, {selected_match['city']}"
            )
            if selected_match["comment"]:
                st.write(f"Rule: {selected_match['comment']}")
    else:
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


def main() -> None:
    st.set_page_config(page_title="Knockout Matchup Predictor", page_icon="⚽", layout="centered")
    st.title("Knockout Matchup Predictor")
    render_matchup_predictor()


if __name__ == "__main__":
    main()
