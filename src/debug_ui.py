from __future__ import annotations

import streamlit as st

try:
    from src.ui import (
        get_round_label,
        load_world_cup_data,
        render_matchup_predictor,
    )
except ModuleNotFoundError:
    from ui import (
        get_round_label,
        load_world_cup_data,
        render_matchup_predictor,
    )


def main() -> None:
    st.set_page_config(page_title="Which Matchup (Debug)", page_icon="⚽", layout="centered")
    st.title("Which Matchup")
    st.caption("Debug view with additional tabs for world cup data exploration.")

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
        render_matchup_predictor()


if __name__ == "__main__":
    main()
