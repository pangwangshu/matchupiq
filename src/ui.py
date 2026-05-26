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


def main() -> None:
    st.set_page_config(page_title="Which Matchup", page_icon="⚽", layout="centered")
    st.title("Which Matchup")
    st.caption("Explore actual published 2026 World Cup participants, groups, and schedule.")

    world_cup = load_world_cup_data()
    participants = world_cup.get("participants", [])
    groups = world_cup.get("groups", [])
    schedule = world_cup.get("schedule", [])

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Countries", "Groups", "Schedule", "Matchup Predictor"]
    )

    with tab1:
        st.subheader("Participating Countries")
        country_options = [p["name"] for p in participants]
        selected_country = st.selectbox("Country", country_options)
        country = next((p for p in participants if p["name"] == selected_country), None)
        if country:
            col1, col2 = st.columns([1, 2])
            with col1:
                st.image(country["flag_url"], width=80)
            with col2:
                st.markdown(f"**{country['name']}**")
                st.write(f"Code: `{country['code3']}`")
                st.write(f"Local flag file: `{country['flag_local']}`")

        st.dataframe(
            [
                {
                    "Country": p["name"],
                    "Code3": p["code3"],
                    "Flag URL": p["flag_url"],
                    "Flag Local": p["flag_local"],
                }
                for p in participants
            ],
            hide_index=True,
            use_container_width=True,
        )

    with tab2:
        st.subheader("Group Composition")
        group_names = [g["group"] for g in groups]
        selected_group = st.selectbox("Group", group_names)
        group_info = next((g for g in groups if g["group"] == selected_group), None)
        if group_info:
            st.write(", ".join(group_info["countries"]))

        st.dataframe(
            [{"Group": g["group"], "Countries": ", ".join(g["countries"])} for g in groups],
            hide_index=True,
            use_container_width=True,
        )

    with tab3:
        st.subheader("Match Schedule")
        stage_filter = st.selectbox(
            "Stage",
            options=["all"] + sorted({m["stage"] for m in schedule}),
            index=0,
        )

        filtered = schedule
        if stage_filter != "all":
            filtered = [m for m in schedule if m["stage"] == stage_filter]

        st.dataframe(
            [
                {
                    "Match": m["match_number"],
                    "Date": m["date"],
                    "Time (Local)": m["local_time"],
                    "City": m["city"],
                    "Stadium": m["stadium"],
                    "Stage": m["stage"],
                    "Group": m["group"],
                    "Matchup": m["matchup"],
                    "Comment": m["comment"],
                }
                for m in filtered
            ],
            hide_index=True,
            use_container_width=True,
        )

    with tab4:
        st.subheader("Knockout Matchup Predictor")
        st.caption("Uses the current prototype scoring model from `data/matches_2026.json`.")
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
        selected = st.selectbox(
            "Select any tournament match",
            options=list(all_match_options.keys()),
            format_func=lambda x: all_match_options[x],
        )

        if st.button("Predict matchup", type="primary"):
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


if __name__ == "__main__":
    main()
