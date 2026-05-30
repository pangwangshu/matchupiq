import json
import os
import re
from datetime import UTC, datetime

import pycountry
import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://worldcuply.com/schedule.html"
OUT_JSON = "data/worldcup_2026_static.json"
FLAGS_DIR = "data/flags"

STAGE_KEY = {
    "Group Stage": "group_stage",
    "Round of 32": "round_of_32",
    "Round of 16": "round_of_16",
    "Quarter-finals": "quarter_finals",
    "Semi-finals": "semi_finals",
    "Final and Third-Place Play-off": "final_and_third_place",
}

# FIFA-style trigrams for non-ISO entities or naming differences.
FIFA_CODE_OVERRIDES = {
    "GB-ENG": "ENG",
    "GB-SCT": "SCO",
    "GB-WLS": "WAL",
    "GB-NIR": "NIR",
}

COUNTRY_NAME_OVERRIDES = {
    "South Korea": "Korea, Republic of",
    "Ivory Coast": "Côte d'Ivoire",
    "Cape Verde": "Cabo Verde",
    "Curaçao": "Curaçao",
    "DR Congo": "Congo, The Democratic Republic of the",
}


def alpha2_to_alpha3(alpha2: str) -> str | None:
    normalized = alpha2.upper()
    if normalized in FIFA_CODE_OVERRIDES:
        return FIFA_CODE_OVERRIDES[normalized]
    match = pycountry.countries.get(alpha_2=normalized)
    return match.alpha_3 if match else None


def country_to_alpha2_alpha3(name: str) -> tuple[str | None, str | None]:
    canonical = COUNTRY_NAME_OVERRIDES.get(name, name)
    try:
        match = (
            pycountry.countries.get(name=canonical)
            or pycountry.countries.get(common_name=canonical)
            or pycountry.countries.get(official_name=canonical)
            or pycountry.countries.search_fuzzy(canonical)[0]
        )
        return match.alpha_2, match.alpha_3
    except Exception:
        return None, None


def main() -> None:
    os.makedirs(FLAGS_DIR, exist_ok=True)

    response = requests.get(SOURCE_URL, timeout=30)
    response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "lxml")

    participants: dict[str, dict] = {}
    groups: dict[str, list[str]] = {}
    schedule: list[dict] = []

    for block in soup.select("section.round-block"):
        stage_title = block.select_one("h2.round-section").get_text(" ", strip=True)
        stage = STAGE_KEY.get(stage_title, stage_title.lower().replace(" ", "_"))
        body = block.select_one("div.round-body")
        current_day = None

        for child in body.children:
            if getattr(child, "name", None) == "h3" and "matchday" in (child.get("class") or []):
                current_day = child.get_text(" ", strip=True)
                continue

            if getattr(child, "name", None) != "div" or "match" not in (child.get("class") or []):
                continue

            head = child.select_one(".match-head").get_text(" ", strip=True)
            match_number = int(re.search(r"Match\s+(\d+)", head).group(1))
            badge = child.select_one(".round-badge").get_text(" ", strip=True)
            team_names = [t.get_text(" ", strip=True) for t in child.select(".team .tn")]
            team_flags = [img.get("src") for img in child.select(".team img.flag")]
            matchup = " vs ".join(team_names)

            meta = child.select_one(".match-meta").get_text(" ", strip=True)
            meta_match = re.search(r"(\d{1,2}:\d{2})\s+venue local\s+(.+)$", meta)
            local_time = meta_match.group(1) if meta_match else None
            venue = meta_match.group(2) if meta_match else None
            stadium, city = (None, None)
            if venue and "," in venue:
                stadium, city = [part.strip() for part in venue.split(",", 1)]

            group = badge if stage == "group_stage" and badge.startswith("Group ") else None
            comment = matchup if stage != "group_stage" else None

            schedule.append(
                {
                    "match_number": match_number,
                    "date": current_day,
                    "local_time": local_time,
                    "city": city,
                    "stadium": stadium,
                    "stage": stage,
                    "group": group,
                    "matchup": matchup,
                    "comment": comment,
                }
            )

            if not group:
                continue

            groups.setdefault(group, [])
            for team_name, flag_url in zip(team_names, team_flags):
                if team_name not in groups[group]:
                    groups[group].append(team_name)

                if team_name in participants:
                    continue

                alpha2 = None
                alpha3 = None

                if flag_url:
                    m = re.search(r"/([a-z]{2}|gb-[a-z]{3})\.png$", flag_url)
                    if m:
                        alpha2 = m.group(1).upper()
                        alpha3 = alpha2_to_alpha3(alpha2)

                if not alpha2 or not alpha3:
                    a2, a3 = country_to_alpha2_alpha3(team_name)
                    alpha2 = alpha2 or a2
                    alpha3 = alpha3 or a3

                local_flag = None
                if alpha2:
                    safe_code = alpha2.lower()
                    local_path = os.path.join(FLAGS_DIR, f"{safe_code}.png")
                    try:
                        r = requests.get(f"https://flagcdn.com/w80/{safe_code}.png", timeout=20)
                        if r.ok:
                            with open(local_path, "wb") as f:
                                f.write(r.content)
                            local_flag = local_path.replace("\\", "/")
                    except Exception:
                        local_flag = None

                participants[team_name] = {
                    "name": team_name,
                    "code3": alpha3,
                    "flag_url": flag_url,
                    "flag_local": local_flag,
                }

    payload = {
        "source": {
            "schedule": SOURCE_URL,
            "official_reference": "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/articles/match-schedule-fixtures-results-teams-stadiums",
        },
        "updated_at": datetime.now(UTC).isoformat(),
        "participants": sorted(participants.values(), key=lambda item: item["name"]),
        "groups": [{"group": key, "countries": sorted(value)} for key, value in sorted(groups.items())],
        "schedule": sorted(schedule, key=lambda item: item["match_number"]),
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(
        f"built participants={len(payload['participants'])}, groups={len(payload['groups'])}, schedule={len(payload['schedule'])}"
    )


if __name__ == "__main__":
    main()
