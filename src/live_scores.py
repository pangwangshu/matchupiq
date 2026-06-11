from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from src.team_name_normalization import TeamNameNormalizer
    from src.tournament import parse_fixed_matchup_text
except ModuleNotFoundError:
    from team_name_normalization import TeamNameNormalizer
    from tournament import parse_fixed_matchup_text

FOOTBALL_DATA_WORLD_CUP_MATCHES_URL = "https://api.football-data.org/v4/competitions/WC/matches"
FOOTBALL_DATA_FINISHED_STATUSES = {"FINISHED", "AWARDED"}


class LiveScoreFetcher(Protocol):
    def fetch_matches(self) -> list[dict[str, Any]]:
        """Fetch provider match rows."""
        ...


@dataclass(frozen=True)
class NormalizedLiveResult:
    match_number: int
    status: str
    played: bool
    home_team: str
    away_team: str
    home_goals: int | None
    away_goals: int | None
    provider_match_id: int | str | None = None
    provider_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_number": self.match_number,
            "status": self.status,
            "played": self.played,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "home_goals": self.home_goals,
            "away_goals": self.away_goals,
            "provider_match_id": self.provider_match_id,
            "provider_status": self.provider_status,
        }


@dataclass(frozen=True)
class LiveScoreSnapshot:
    provider: str
    fetched_at_epoch: float
    results: dict[int, NormalizedLiveResult]
    unmatched_provider_matches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def completed_count(self) -> int:
        return sum(1 for result in self.results.values() if result.played)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "fetched_at_epoch": self.fetched_at_epoch,
            "results": {str(match_number): result.to_dict() for match_number, result in sorted(self.results.items())},
            "unmatched_provider_matches": self.unmatched_provider_matches,
        }


@dataclass(frozen=True)
class LiveScoreSnapshotStatus:
    provider: str
    has_snapshot: bool
    fetched_at_epoch: float | None
    matched_count: int
    completed_count: int
    unmatched_count: int
    last_refresh_attempt_at: float | None
    last_refresh_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "has_snapshot": self.has_snapshot,
            "fetched_at_epoch": self.fetched_at_epoch,
            "matched_count": self.matched_count,
            "completed_count": self.completed_count,
            "unmatched_count": self.unmatched_count,
            "last_refresh_attempt_at": self.last_refresh_attempt_at,
            "last_refresh_error": self.last_refresh_error,
        }


class FootballDataLiveScoreFetcher:
    """Fetch World Cup match rows from football-data.org."""

    def __init__(
        self,
        *,
        api_token: str | None = None,
        season: int = 2026,
        base_url: str = FOOTBALL_DATA_WORLD_CUP_MATCHES_URL,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_token = api_token
        self.season = season
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def _api_token(self) -> str:
        token = self.api_token or os.getenv("FOOTBALL_DATA_TOKEN")
        if not token:
            raise RuntimeError("FOOTBALL_DATA_TOKEN is not set.")
        return token

    def fetch_matches(self) -> list[dict[str, Any]]:
        headers = {"X-Auth-Token": self._api_token()}
        separator = "&" if "?" in self.base_url else "?"
        url = f"{self.base_url}{separator}{urlencode({'season': self.season})}"
        request = Request(url, headers=headers, method="GET")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.load(response)
        matches = payload.get("matches", [])
        if not isinstance(matches, list):
            raise RuntimeError("football-data.org response did not contain a matches list.")
        return [match for match in matches if isinstance(match, dict)]


def _parse_provider_date(raw: object) -> str | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).date().isoformat()


def _parse_local_date(raw: object) -> str | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%A %d %B %Y").date().isoformat()
    except ValueError:
        return None


def _to_int_or_none(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_float_or_none(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _stage_matches(provider_stage: str | None, local_stage: str | None) -> bool:
    if not provider_stage or not local_stage:
        return True
    provider = provider_stage.upper()
    local = local_stage.lower()
    return (
        (provider == "GROUP_STAGE" and local == "group_stage")
        or (provider == "LAST_32" and local == "round_of_32")
        or (provider == "LAST_16" and local == "round_of_16")
        or (provider == "QUARTER_FINALS" and local == "quarter_finals")
        or (provider == "SEMI_FINALS" and local == "semi_finals")
        or (provider in {"THIRD_PLACE", "FINAL"} and local == "final_and_third_place")
    )


def build_live_score_snapshot(
    *,
    provider_matches: list[dict[str, Any]],
    world_cup_data: dict,
    normalizer: TeamNameNormalizer,
    fetched_at_epoch: float | None = None,
    provider: str = "football-data.org",
) -> LiveScoreSnapshot:
    """Match provider rows to local scheduled match numbers and normalize scores."""
    local_fixed_matches: list[dict[str, Any]] = []
    for local in world_cup_data.get("schedule", []):
        if not isinstance(local, dict) or not isinstance(local.get("match_number"), int):
            continue
        fixed = parse_fixed_matchup_text(str(local.get("matchup") or local.get("comment") or ""))
        if fixed is None:
            continue
        home = normalizer.resolve(fixed[0])
        away = normalizer.resolve(fixed[1])
        if home is None or away is None:
            continue
        local_fixed_matches.append(
            {
                "match_number": int(local["match_number"]),
                "home_team": home,
                "away_team": away,
                "date": _parse_local_date(local.get("date")),
                "stage": str(local.get("stage") or ""),
            }
        )

    results: dict[int, NormalizedLiveResult] = {}
    unmatched: list[dict[str, Any]] = []
    for provider_match in provider_matches:
        home_team_payload = provider_match.get("homeTeam")
        away_team_payload = provider_match.get("awayTeam")
        home_raw = home_team_payload.get("name") if isinstance(home_team_payload, dict) else None
        away_raw = away_team_payload.get("name") if isinstance(away_team_payload, dict) else None
        home = normalizer.resolve(str(home_raw or ""))
        away = normalizer.resolve(str(away_raw or ""))
        provider_date = _parse_provider_date(provider_match.get("utcDate"))
        provider_stage = str(provider_match.get("stage") or "")
        provider_status = str(provider_match.get("status") or "").upper()
        provider_id = provider_match.get("id")

        if home is None or away is None:
            unmatched.append(
                {
                    "provider_match_id": provider_id,
                    "home_team": home_raw,
                    "away_team": away_raw,
                    "status": provider_status,
                    "reason": "team_name_not_recognized",
                }
            )
            continue

        candidates = [
            local
            for local in local_fixed_matches
            if local["home_team"] == home
            and local["away_team"] == away
            and _stage_matches(provider_stage, local["stage"])
        ]
        dated_candidates = [local for local in candidates if provider_date and local["date"] == provider_date]
        if dated_candidates:
            candidates = dated_candidates

        if len(candidates) != 1:
            unmatched.append(
                {
                    "provider_match_id": provider_id,
                    "home_team": home_raw,
                    "away_team": away_raw,
                    "status": provider_status,
                    "reason": "no_unique_local_match",
                }
            )
            continue

        score_payload = provider_match.get("score")
        if not isinstance(score_payload, dict):
            score_payload = {}
        full_time = score_payload.get("fullTime", {})
        if not isinstance(full_time, dict):
            full_time = {}
        home_goals = _to_int_or_none(full_time.get("home"))
        away_goals = _to_int_or_none(full_time.get("away"))
        played = (
            provider_status in FOOTBALL_DATA_FINISHED_STATUSES
            and home_goals is not None
            and away_goals is not None
        )
        match_number = int(candidates[0]["match_number"])
        results[match_number] = NormalizedLiveResult(
            match_number=match_number,
            status="completed" if played else "pending",
            played=played,
            home_team=home,
            away_team=away,
            home_goals=home_goals,
            away_goals=away_goals,
            provider_match_id=provider_id,
            provider_status=provider_status,
        )

    return LiveScoreSnapshot(
        provider=provider,
        fetched_at_epoch=fetched_at_epoch if fetched_at_epoch is not None else time.time(),
        results=results,
        unmatched_provider_matches=unmatched,
    )


class LiveScoreSnapshotStore:
    """Persists the latest normalized live score snapshot."""

    def __init__(
        self,
        *,
        fetcher: LiveScoreFetcher | None = None,
        snapshot_path: Path | None = None,
        provider: str = "football-data.org",
        clock: Any = time.time,
    ) -> None:
        self.fetcher = fetcher or FootballDataLiveScoreFetcher()
        self.snapshot_path = snapshot_path
        self.provider = provider
        self.clock = clock
        self._last_refresh_attempt_at: float | None = None
        self._last_refresh_error: str | None = None

    def refresh_now(self, *, world_cup_data: dict, normalizer: TeamNameNormalizer) -> LiveScoreSnapshot:
        now = float(self.clock())
        self._last_refresh_attempt_at = now
        self._last_refresh_error = None
        try:
            provider_matches = self.fetcher.fetch_matches()
            snapshot = build_live_score_snapshot(
                provider_matches=provider_matches,
                world_cup_data=world_cup_data,
                normalizer=normalizer,
                fetched_at_epoch=now,
                provider=self.provider,
            )
            self._write_snapshot(snapshot)
            return snapshot
        except Exception as exc:
            self._last_refresh_error = str(exc)
            raise

    def load_snapshot_payload(self) -> dict[str, Any]:
        if self.snapshot_path is None or not self.snapshot_path.exists():
            return {}
        try:
            with self.snapshot_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def status(self) -> LiveScoreSnapshotStatus:
        payload = self.load_snapshot_payload()
        results = payload.get("results", {})
        unmatched = payload.get("unmatched_provider_matches", [])
        if not isinstance(results, dict):
            results = {}
        if not isinstance(unmatched, list):
            unmatched = []
        completed_count = sum(
            1
            for item in results.values()
            if isinstance(item, dict) and bool(item.get("played"))
        )
        return LiveScoreSnapshotStatus(
            provider=str(payload.get("provider") or self.provider),
            has_snapshot=bool(payload),
            fetched_at_epoch=_to_float_or_none(payload.get("fetched_at_epoch")),
            matched_count=len(results),
            completed_count=completed_count,
            unmatched_count=len(unmatched),
            last_refresh_attempt_at=self._last_refresh_attempt_at,
            last_refresh_error=self._last_refresh_error,
        )

    def _write_snapshot(self, snapshot: LiveScoreSnapshot) -> None:
        if self.snapshot_path is None:
            return
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with self.snapshot_path.open("w", encoding="utf-8") as f:
            json.dump(snapshot.to_dict(), f, indent=2, sort_keys=True)
            f.write("\n")
