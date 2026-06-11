from __future__ import annotations

import base64
import inspect
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

try:
    from src.engine import MatchupPredictor, PredictorRuntimeConfig
    from src.prediction_cache import PredictionCacheService
except ModuleNotFoundError:
    from engine import MatchupPredictor, PredictorRuntimeConfig
    from prediction_cache import PredictionCacheService

MATCHUP_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "matches_2026.json"
WORLD_CUP_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "worldcup_2026_static.json"
DEFAULT_STRENGTH_MODE = "market"
DEFAULT_MARKET_TTL_SECONDS = 900.0
FLAG_IMAGE_STYLE = "width:30px; height:20px; object-fit:cover; border-radius:6px;"
MATCHUP_ROW_STYLE = (
    "display:grid; grid-template-columns: minmax(0, 1fr) 40px 56px 40px minmax(0, 1fr); "
    "align-items:center; column-gap:10px; margin:8px 0;"
)
TEAM_NAME_STYLE = (
    "font-weight:700; white-space:normal; overflow-wrap:anywhere; line-height:1.2;"
)
VS_STYLE = "text-align:center; font-weight:800; letter-spacing:0.5px;"
DATA_SOURCE_STYLE = """
<style>
  .data-source-panel {
    margin-top: 1.35rem;
    padding-top: 1rem;
    border-top: 1px solid rgba(128, 128, 128, 0.24);
    color: rgba(250, 250, 250, 0.72);
  }
  .data-source-title {
    margin-bottom: 0.45rem;
    font-size: 0.82rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: rgba(250, 250, 250, 0.62);
  }
  .data-source-grid {
    display: grid;
    gap: 0.45rem;
  }
  .data-source-row {
    display: grid;
    grid-template-columns: minmax(112px, 0.34fr) minmax(0, 1fr);
    gap: 0.75rem;
    align-items: baseline;
    font-size: 0.92rem;
    line-height: 1.45;
  }
  .data-source-label {
    font-weight: 700;
    color: rgba(250, 250, 250, 0.86);
  }
  .data-source-detail {
    color: rgba(250, 250, 250, 0.64);
  }
  @media (prefers-color-scheme: light) {
    .data-source-panel {
      color: rgba(49, 51, 63, 0.72);
    }
    .data-source-title {
      color: rgba(49, 51, 63, 0.62);
    }
    .data-source-label {
      color: rgba(49, 51, 63, 0.88);
    }
    .data-source-detail {
      color: rgba(49, 51, 63, 0.66);
    }
  }
  @media (max-width: 640px) {
    .data-source-row {
      grid-template-columns: 1fr;
      gap: 0.05rem;
    }
  }
</style>
"""
logger = logging.getLogger(__name__)


def render_flag_html(flag_b64: str) -> str:
    return (
        f"<img src='data:image/png;base64,{flag_b64}' "
        f"style='{FLAG_IMAGE_STYLE}' />"
    )


def render_matchup_html(home_team: str, away_team: str, home_flag_html: str, away_flag_html: str) -> str:
    return f"""
        <div style="{MATCHUP_ROW_STYLE}">
          <div style="text-align:right; {TEAM_NAME_STYLE}">{home_team}</div>
          <div style="display:flex; justify-content:center;">{home_flag_html}</div>
          <div style="{VS_STYLE}">VS</div>
          <div style="display:flex; justify-content:center;">{away_flag_html}</div>
          <div style="text-align:left; {TEAM_NAME_STYLE}">{away_team}</div>
        </div>
    """


@st.cache_resource
def get_predictor(strength_mode: str, market_ttl_seconds: float) -> MatchupPredictor:
    predictor = MatchupPredictor(
        runtime_config=PredictorRuntimeConfig(
            strength_mode=strength_mode,  # type: ignore[arg-type]
            market_fresh_ttl_seconds=market_ttl_seconds,
            max_market_age_seconds=market_ttl_seconds,
        )
    )
    if strength_mode != "fifa":
        try:
            predictor.refresh_polymarket_snapshot()
        except Exception:
            logger.exception("startup_polymarket_refresh_failed")
    return predictor


@st.cache_resource
def get_prediction_cache(strength_mode: str, market_ttl_seconds: float) -> PredictionCacheService:
    return PredictionCacheService(
        predictor=get_predictor(strength_mode, market_ttl_seconds),
    )


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


def format_epoch(epoch: float | None) -> str:
    if epoch is None:
        return "Never"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def live_score_status_text(predictor: MatchupPredictor) -> tuple[str, str | None]:
    score_status = predictor.live_score_status()
    score_freshness = "available" if score_status.get("has_snapshot") else "missing"
    return (
        (
            f"Live scores: {score_freshness}; "
            f"{score_status.get('matched_count', 0)} matched, "
            f"{score_status.get('completed_count', 0)} completed, "
            f"{score_status.get('unmatched_count', 0)} unmatched; "
            f"last refresh {format_epoch(score_status.get('fetched_at_epoch'))}."
        ),
        score_status.get("last_refresh_error"),
    )


def live_score_status_parts(predictor: MatchupPredictor) -> tuple[str, str, str | None]:
    score_status = predictor.live_score_status()
    score_freshness = "available" if score_status.get("has_snapshot") else "missing"
    return (
        "Live scores",
        (
            f"{score_freshness}; {score_status.get('matched_count', 0)} matched, "
            f"{score_status.get('completed_count', 0)} completed, "
            f"{score_status.get('unmatched_count', 0)} unmatched; "
            f"last refresh {format_epoch(score_status.get('fetched_at_epoch'))}"
        ),
        score_status.get("last_refresh_error"),
    )


def polymarket_status_text(predictor: MatchupPredictor) -> tuple[str | None, str | None]:
    snapshot_status = predictor.polymarket_snapshot_status()
    if snapshot_status is None:
        return None, None

    freshness = "fresh" if snapshot_status.get("is_fresh") else "stale"
    if not snapshot_status.get("has_snapshot"):
        freshness = "missing"
    return (
        (
            f"Polymarket snapshot: {freshness}; "
            f"{snapshot_status.get('market_count', 0)} markets from "
            f"{snapshot_status.get('events_seen', 0)} events; "
            f"last refresh {format_epoch(snapshot_status.get('fetched_at_epoch'))}."
        ),
        snapshot_status.get("last_refresh_error"),
    )


def polymarket_status_parts(predictor: MatchupPredictor) -> tuple[str, str, str | None] | None:
    snapshot_status = predictor.polymarket_snapshot_status()
    if snapshot_status is None:
        return None

    freshness = "fresh" if snapshot_status.get("is_fresh") else "stale"
    if not snapshot_status.get("has_snapshot"):
        freshness = "missing"
    return (
        "Market snapshot",
        (
            f"{freshness}; {snapshot_status.get('market_count', 0)} markets from "
            f"{snapshot_status.get('events_seen', 0)} events; "
            f"last refresh {format_epoch(snapshot_status.get('fetched_at_epoch'))}"
        ),
        snapshot_status.get("last_refresh_error"),
    )


def _render_data_source_rows(rows: list[tuple[str, str]]) -> None:
    row_html = "\n".join(
        (
            "<div class='data-source-row'>"
            f"<div class='data-source-label'>{label}</div>"
            f"<div class='data-source-detail'>{detail}</div>"
            "</div>"
        )
        for label, detail in rows
    )
    st.markdown(
        (
            f"{DATA_SOURCE_STYLE}\n"
            "<div class='data-source-panel'>\n"
            "  <div class='data-source-title'>Data sources</div>\n"
            "  <div class='data-source-grid'>\n"
            f"{row_html}\n"
            "  </div>\n"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_runtime_status(
    predictor: MatchupPredictor,
    signal_status: dict | None = None,
) -> None:
    rows: list[tuple[str, str]] = []
    score_label, score_detail, score_error = live_score_status_parts(predictor)
    rows.append((score_label, score_detail))

    if score_error:
        st.warning(f"{score_label}: {score_detail}. Last refresh error: {score_error}")

    market_parts = polymarket_status_parts(predictor)
    if market_parts:
        market_label, market_detail, market_error = market_parts
        rows.append((market_label, market_detail))
        if market_error:
            st.warning(f"{market_label}: {market_detail}. Last refresh error: {market_error}")

    if signal_status:
        market_hits = signal_status.get("market_hits", 0)
        fallback_hits = signal_status.get("fallback_hits", 0)
        actual_score_hits = signal_status.get("actual_score_hits", 0)
        rows.append(
            (
                "Prediction signals",
                f"market data in {market_hits} branches; FIFA fallback in {fallback_hits} branches",
            )
        )
        rows.append(("Actual scores", f"used in {actual_score_hits} branches"))

    _render_data_source_rows(rows)


def clear_prediction_caches(strength_mode: str, market_ttl_seconds: float) -> None:
    get_prediction_cache(strength_mode, market_ttl_seconds).clear()
    if (
        strength_mode != DEFAULT_STRENGTH_MODE
        or market_ttl_seconds != DEFAULT_MARKET_TTL_SECONDS
    ):
        get_prediction_cache(DEFAULT_STRENGTH_MODE, DEFAULT_MARKET_TTL_SECONDS).clear()


def render_admin_controls() -> None:
    st.set_page_config(page_title="MatchupIQ Admin", page_icon="⚽", layout="centered")
    st.title("MatchupIQ Admin")

    if hasattr(st, "segmented_control"):
        strength_mode = st.segmented_control(
            "Strength mode",
            options=["market", "hybrid", "fifa"],
            default=DEFAULT_STRENGTH_MODE,
            key="admin_strength_mode_control",
        )
    else:
        strength_mode = st.radio(
            "Strength mode",
            options=["market", "hybrid", "fifa"],
            index=0,
            horizontal=True,
            key="admin_strength_mode_control",
        )
    strength_mode = str(strength_mode or DEFAULT_STRENGTH_MODE)
    market_ttl_seconds = float(
        st.number_input(
            "Market TTL seconds",
            min_value=60,
            max_value=7200,
            value=int(DEFAULT_MARKET_TTL_SECONDS),
            step=60,
            key="admin_market_ttl_seconds_input",
            disabled=strength_mode == "fifa",
        )
    )

    predictor = get_predictor(strength_mode, market_ttl_seconds)
    refresh_col1, refresh_col2 = st.columns(2)
    with refresh_col1:
        refresh_market_clicked = st.button(
            "Refresh market",
            key="admin_refresh_polymarket_button",
            disabled=strength_mode == "fifa",
            use_container_width=True,
        )
    with refresh_col2:
        refresh_scores_clicked = st.button(
            "Refresh scores",
            key="admin_refresh_scores_button",
            use_container_width=True,
        )

    if refresh_market_clicked:
        try:
            predictor.refresh_polymarket_snapshot()
            st.success("Polymarket snapshot refreshed.")
        except Exception as exc:
            st.error(f"Polymarket refresh failed: {exc}")

    if refresh_scores_clicked:
        try:
            predictor.refresh_live_scores()
            clear_prediction_caches(strength_mode, market_ttl_seconds)
            st.success("Live scores refreshed.")
        except Exception as exc:
            st.error(f"Live score refresh failed: {exc}")

    render_runtime_status(predictor)


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

    strength_mode = DEFAULT_STRENGTH_MODE
    market_ttl_seconds = DEFAULT_MARKET_TTL_SECONDS
    predictor = get_predictor(strength_mode, market_ttl_seconds)

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
                home_flag_html = render_flag_html(home_flag_b64)
            if away_flag:
                away_flag_b64 = encode_flag_image(away_flag)
                away_flag_html = render_flag_html(away_flag_b64)

            st.markdown(
                render_matchup_html(home_team, away_team, home_flag_html, away_flag_html),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(f"**{matchup_text}**")
        match_details = (
            f"{selected_match['date']} {selected_match['local_time']} | "
            f"{selected_match['stadium']}, {selected_match['city']}"
        )
        st.write(match_details)
        return

    prediction_cache = get_prediction_cache(strength_mode, market_ttl_seconds)
    result = prediction_cache.get_prediction(selected)

    st.info("Top 10 predicted matchups")
    for candidate in result.top_candidates:
        home_flag = team_to_flag.get(candidate.home_team)
        away_flag = team_to_flag.get(candidate.away_team)
        home_flag_html = ""
        away_flag_html = ""
        if home_flag:
            home_flag_b64 = encode_flag_image(home_flag)
            home_flag_html = render_flag_html(home_flag_b64)
        if away_flag:
            away_flag_b64 = encode_flag_image(away_flag)
            away_flag_html = render_flag_html(away_flag_b64)

        st.markdown(
            render_matchup_html(
                candidate.home_team,
                candidate.away_team,
                home_flag_html,
                away_flag_html,
            ),
            unsafe_allow_html=True,
        )
    render_runtime_status(predictor, result.signal_status)


def main() -> None:
    st.set_page_config(page_title="MatchupIQ", page_icon="⚽", layout="centered")
    st.title("MatchupIQ")
    st.caption("Predict every 2026 World Cup clash in seconds.")
    render_matchup_predictor()


if __name__ == "__main__":
    main()
