from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import requests

SOURCE_URL = "https://inside.fifa.com/fifa-world-ranking/men"
RANKING_API_URL = "https://inside.fifa.com/api/ranking-overview"
DEFAULT_PARTICIPANTS_JSON = Path("data/worldcup_2026_static.json")
DEFAULT_OUT_JSON = Path("data/fifa_men_ranking_static.json")

# Canonicalization for participant names that differ from FIFA ranking naming.
PARTICIPANT_NAME_TO_FIFA_NAME = {
    "Ivory Coast": "Cote d'Ivoire",
    "South Korea": "Korea Republic",
}

# Canonicalization for mismatched country codes in existing static participant file.
PARTICIPANT_CODE_OVERRIDES = {
    "England": "ENG",
    "Scotland": "SCO",
    "Ivory Coast": "CIV",
    "South Korea": "KOR",
}

HEADERS_HTML = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://inside.fifa.com/",
}

HEADERS_API = {
    "User-Agent": HEADERS_HTML["User-Agent"],
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SOURCE_URL,
}


class ParseError(RuntimeError):
    pass


def _extract_next_data_json(html: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    if not match:
        raise ParseError("Unable to locate __NEXT_DATA__ payload on FIFA ranking page.")
    return json.loads(match.group(1))


def _fetch_date_ids(session: requests.Session) -> list[dict]:
    response = session.get(SOURCE_URL, headers=HEADERS_HTML, timeout=30)
    response.raise_for_status()

    page_data = _extract_next_data_json(response.text)
    all_dates = (
        page_data.get("props", {})
        .get("pageProps", {})
        .get("pageData", {})
        .get("ranking", {})
        .get("allAvailableDates", [])
    )

    if not all_dates:
        raise ParseError("No ranking date IDs found on FIFA ranking page payload.")

    valid_dates = [d for d in all_dates if isinstance(d, dict) and d.get("id")]
    if not valid_dates:
        raise ParseError("Ranking date entries are present but do not contain valid IDs.")

    return valid_dates


def _fetch_rankings_for_date(session: requests.Session, date_id: str) -> list[dict]:
    response = session.get(
        RANKING_API_URL,
        params={"locale": "en", "dateId": date_id},
        headers=HEADERS_API,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("rankings", [])


def _pick_latest_nonempty_rankings(session: requests.Session, date_entries: list[dict]) -> tuple[dict, list[dict]]:
    for date_entry in date_entries:
        rankings = _fetch_rankings_for_date(session, date_entry["id"])
        if rankings:
            return date_entry, rankings
    raise ParseError("No non-empty ranking payload found for any available FIFA dateId.")


def _normalize_participant_code(participant: dict) -> str | None:
    name = participant.get("name")
    if name in PARTICIPANT_CODE_OVERRIDES:
        return PARTICIPANT_CODE_OVERRIDES[name]
    return participant.get("code3")


def _load_participants(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    participants = payload.get("participants", [])
    if len(participants) != 48:
        raise ParseError(f"Expected 48 participants, found {len(participants)} in {path}.")
    return participants


def _build_output(rankings: list[dict], participants: list[dict], date_entry: dict) -> dict:
    parsed_rows: list[dict] = []
    by_code: dict[str, dict] = {}
    by_name: dict[str, dict] = {}

    for row in rankings:
        item = row.get("rankingItem", {})
        country = item.get("name")
        code = item.get("countryCode")
        rank = item.get("rank")
        points = item.get("totalPoints")

        if country is None or code is None or rank is None or points is None:
            continue

        parsed = {
            "rank": int(rank),
            "country": str(country),
            "country_code": str(code),
            "points": float(points),
        }
        parsed_rows.append(parsed)
        by_code[parsed["country_code"]] = parsed
        by_name[parsed["country"].lower()] = parsed

    if not parsed_rows:
        raise ParseError("Ranking payload did not contain any parseable rank/country/points rows.")

    missing: list[str] = []
    participants_ranking: list[dict] = []

    for participant in sorted(participants, key=lambda x: x["name"]):
        participant_name = participant["name"]
        participant_code = _normalize_participant_code(participant)
        fifa_name = PARTICIPANT_NAME_TO_FIFA_NAME.get(participant_name, participant_name)

        match_row = None
        if participant_code:
            match_row = by_code.get(participant_code)
        if not match_row:
            match_row = by_name.get(fifa_name.lower())

        if not match_row:
            missing.append(participant_name)
            continue

        participants_ranking.append(
            {
                "participant_name": participant_name,
                "rank": match_row["rank"],
                "country": match_row["country"],
                "country_code": match_row["country_code"],
                "points": match_row["points"],
            }
        )

    if missing:
        raise ParseError(
            "Missing FIFA rankings for participants: " + ", ".join(missing)
        )

    participants_ranking.sort(key=lambda x: x["rank"])
    parsed_rows.sort(key=lambda x: x["rank"])

    return {
        "source": {
            "ranking_page": SOURCE_URL,
            "ranking_api": RANKING_API_URL,
            "locale": "en",
            "date_id": date_entry.get("id"),
            "ranking_date": date_entry.get("date"),
            "match_window_end_date": date_entry.get("matchWindowEndDate"),
        },
        "updated_at": datetime.now(UTC).isoformat(),
        "total_rows": len(parsed_rows),
        "rankings": parsed_rows,
        "participants_total": len(participants),
        "participants_rankings": participants_ranking,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse FIFA men's ranking data and validate 48 participants.")
    parser.add_argument(
        "--participants-json",
        type=Path,
        default=DEFAULT_PARTICIPANTS_JSON,
        help=f"Path to world cup participant data (default: {DEFAULT_PARTICIPANTS_JSON})",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=DEFAULT_OUT_JSON,
        help=f"Output JSON path (default: {DEFAULT_OUT_JSON})",
    )
    args = parser.parse_args()

    participants = _load_participants(args.participants_json)

    with requests.Session() as session:
        date_entries = _fetch_date_ids(session)
        selected_date, rankings = _pick_latest_nonempty_rankings(session, date_entries)

    payload = _build_output(rankings, participants, selected_date)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "built "
        f"date_id={payload['source']['date_id']} "
        f"ranking_date={payload['source']['ranking_date']} "
        f"rows={payload['total_rows']} "
        f"participants={payload['participants_total']}"
    )


if __name__ == "__main__":
    main()
