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


def get_match_category(match: dict) -> str:
    stage = match.get("stage")
    if stage == "group_stage":
        return "Group Stage"
    if stage == "round_of_32":
        return "Round of 32"
    if stage == "round_of_16":
        return "Round of 16"
    if stage == "quarter_finals":
        return "Quarter-Final"
    if stage == "semi_finals":
        return "Semi-Final"
    if stage == "final_and_third_place":
        matchup = str(match.get("matchup", "")).lower()
        if matchup.startswith("loser"):
            return "3rd Place"
        return "Final"
    return str(stage).replace("_", " ").title()


def order_categories(categories: set[str]) -> list[str]:
    preferred_order = [
        "Group Stage",
        "Round of 32",
        "Round of 16",
        "Round of 8",
        "Semi-Final",
        "3rd Place",
        "Final",
    ]
    alias_map = {"Quarter-Final": "Round of 8"}

    normalized_to_original: dict[str, str] = {}
    for category in categories:
        normalized = alias_map.get(category, category)
        if normalized not in normalized_to_original:
            normalized_to_original[normalized] = normalized

    ordered = [c for c in preferred_order if c in normalized_to_original]
    extras = sorted(c for c in normalized_to_original if c not in preferred_order)
    return ordered + extras


def render_matchup_predictor() -> None:
    world_cup = load_world_cup_data()
    schedule = world_cup.get("schedule", [])

    options = load_match_options()
    option_match_ids = set(options.keys())
    city_filter_options = ["All Cities"] + sorted({m["city"] for m in schedule})
    category_filter_options = ["All Categories"] + order_categories(
        {get_match_category(m) for m in schedule}
    )

    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        selected_city = st.selectbox("Filter by city", options=city_filter_options, index=0)
    with filter_col2:
        selected_category = st.selectbox(
            "Filter by category",
            options=category_filter_options,
            index=0,
        )

    filtered_schedule = schedule
    if selected_city != "All Cities":
        filtered_schedule = [m for m in filtered_schedule if m["city"] == selected_city]
    if selected_category != "All Categories":
        filtered_schedule = [
            m
            for m in filtered_schedule
            if (
                get_match_category(m) == selected_category
                or (
                    selected_category == "Round of 8"
                    and get_match_category(m) == "Quarter-Final"
                )
            )
        ]

    all_match_options = {
        str(m["match_number"]): (
            f"Match {m['match_number']} - {to_short_date(m['date'])} - {m['city']} - "
            f"{get_round_label(m)} - "
            f"{m['matchup']}"
        )
        for m in filtered_schedule
    }
    if not all_match_options:
        st.info("No matches found for the selected filters.")
        return

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
