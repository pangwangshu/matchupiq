from __future__ import annotations

import base64
import json
import inspect
import re
from pathlib import Path
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

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
    participants = world_cup.get("participants", [])
    schedule = world_cup.get("schedule", [])
    team_to_flag = {
        p["name"]: str((Path(__file__).resolve().parent.parent / p["flag_local"]).resolve())
        for p in participants
        if p.get("name") and p.get("flag_local")
    }

    @st.cache_data
    def encode_flag_image(path: str) -> str:
        with Path(path).open("rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    city_filter_options = ["All Cities"] + sorted({m["city"] for m in schedule})
    category_filter_options = ["All Categories"] + order_categories(
        {get_match_category(m) for m in schedule}
    )
    supports_filter_mode = "filter_mode" in inspect.signature(st.selectbox).parameters
    selectbox_kwargs = {"filter_mode": None} if supports_filter_mode else {}

    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        selected_city = st.selectbox(
            "Filter by city",
            options=city_filter_options,
            index=0,
            key="city_filter_select",
            **selectbox_kwargs,
        )
    with filter_col2:
        selected_category = st.selectbox(
            "Filter by category",
            options=category_filter_options,
            index=0,
            key="category_filter_select",
            **selectbox_kwargs,
        )
    include_group_stage = st.checkbox("Include group stage matches", value=False)

    filtered_schedule = schedule
    if not include_group_stage:
        filtered_schedule = [
            m for m in filtered_schedule if get_match_category(m) != "Group Stage"
        ]
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
        key="match_picker_select",
        **selectbox_kwargs,
    )

    components.html(
        """
        <script>
          const applyNoKeyboard = () => {
            const selectors = [
              "div[data-baseweb='select'] input[role='combobox']",
              ".stSelectbox input[role='combobox']",
            ];
            const seen = new Set();
            for (const selector of selectors) {
              const elements = window.parent.document.querySelectorAll(selector);
              for (const el of elements) {
                if (seen.has(el)) continue;
                seen.add(el);
                el.readOnly = true;
                el.setAttribute("readonly", "readonly");
                el.setAttribute("inputmode", "none");
                el.setAttribute("autocomplete", "off");
                el.style.caretColor = "transparent";
              }
            }
          };
          applyNoKeyboard();
          setTimeout(applyNoKeyboard, 300);
          setTimeout(applyNoKeyboard, 1000);
          setTimeout(applyNoKeyboard, 1800);
        </script>
        """,
        height=0,
    )

    if selected == placeholder:
        return

    selected_match = next((m for m in schedule if str(m["match_number"]) == selected), None)
    if selected_match and selected_match.get("stage") == "group_stage":
        st.success("Group stage matchup is fixed")
        matchup_text = selected_match.get("matchup", "")
        parts = re.split(r"\s+vs\s+", matchup_text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            home_team, away_team = parts[0].strip(), parts[1].strip()
            home_flag = team_to_flag.get(home_team)
            away_flag = team_to_flag.get(away_team)
            home_flag_html = ""
            away_flag_html = ""
            if home_flag:
                home_flag_b64 = encode_flag_image(home_flag)
                home_flag_html = f"<img src='data:image/png;base64,{home_flag_b64}' style='width:30px; height:20px; object-fit:cover; border-radius:6px;' />"
            if away_flag:
                away_flag_b64 = encode_flag_image(away_flag)
                away_flag_html = f"<img src='data:image/png;base64,{away_flag_b64}' style='width:30px; height:20px; object-fit:cover; border-radius:6px;' />"

            st.markdown(
                f"""
                <div style="display:grid; grid-template-columns: minmax(0, 1fr) 40px 56px 40px minmax(0, 1fr); align-items:center; column-gap:10px; margin:8px 0;">
                  <div style="text-align:right; font-weight:700; white-space:normal; overflow-wrap:anywhere; line-height:1.2;">{home_team}</div>
                  <div style="display:flex; justify-content:center;">{home_flag_html}</div>
                  <div style="text-align:center; font-weight:800; letter-spacing:0.5px;">VS</div>
                  <div style="display:flex; justify-content:center;">{away_flag_html}</div>
                  <div style="text-align:left; font-weight:700; white-space:normal; overflow-wrap:anywhere; line-height:1.2;">{away_team}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(f"**{matchup_text}**")
        st.write(
            f"{selected_match['date']} {selected_match['local_time']} | {selected_match['stadium']}, {selected_match['city']}"
        )
        return

    predictor = get_predictor()
    result = predictor.predict(selected)

    st.info("Top 10 predicted matchups")
    for candidate in result.top_candidates:
        home_flag = team_to_flag.get(candidate.home_team)
        away_flag = team_to_flag.get(candidate.away_team)
        home_flag_html = ""
        away_flag_html = ""
        if home_flag:
            home_flag_b64 = encode_flag_image(home_flag)
            home_flag_html = f"<img src='data:image/png;base64,{home_flag_b64}' style='width:30px; height:20px; object-fit:cover; border-radius:6px;' />"
        if away_flag:
            away_flag_b64 = encode_flag_image(away_flag)
            away_flag_html = f"<img src='data:image/png;base64,{away_flag_b64}' style='width:30px; height:20px; object-fit:cover; border-radius:6px;' />"

        st.markdown(
            f"""
            <div style="display:grid; grid-template-columns: minmax(0, 1fr) 40px 56px 40px minmax(0, 1fr); align-items:center; column-gap:10px; margin:8px 0;">
              <div style="text-align:right; font-weight:700; white-space:normal; overflow-wrap:anywhere; line-height:1.2;">{candidate.home_team}</div>
              <div style="display:flex; justify-content:center;">{home_flag_html}</div>
              <div style="text-align:center; font-weight:800; letter-spacing:0.5px;">VS</div>
              <div style="display:flex; justify-content:center;">{away_flag_html}</div>
              <div style="text-align:left; font-weight:700; white-space:normal; overflow-wrap:anywhere; line-height:1.2;">{candidate.away_team}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def main() -> None:
    st.set_page_config(page_title="World Cup 2026 Matchup Predictor", page_icon="⚽", layout="centered")
    st.title("World Cup 2026 Matchup Predictor")
    render_matchup_predictor()


if __name__ == "__main__":
    main()
