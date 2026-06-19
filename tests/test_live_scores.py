from __future__ import annotations

from src.live_scores import build_live_score_snapshot
from src.team_name_normalization import TeamNameNormalizer


def _world_cup_data() -> dict:
    return {
        "schedule": [
            {
                "match_number": 1,
                "date": "Thursday 11 June 2026",
                "stage": "group_stage",
                "matchup": "Mexico vs South Africa",
            },
            {
                "match_number": 2,
                "date": "Friday 12 June 2026",
                "stage": "group_stage",
                "matchup": "South Korea vs Germany",
            },
        ]
    }


def _normalizer() -> TeamNameNormalizer:
    return TeamNameNormalizer.build(
        canonical_names=["Mexico", "South Africa", "South Korea", "Germany"],
    )


def test_pending_football_data_match_does_not_become_played() -> None:
    snapshot = build_live_score_snapshot(
        provider_matches=[
            {
                "id": 1001,
                "utcDate": "2026-06-11T19:00:00Z",
                "status": "TIMED",
                "stage": "GROUP_STAGE",
                "homeTeam": {"name": "Mexico"},
                "awayTeam": {"name": "South Africa"},
                "score": {"fullTime": {"home": None, "away": None}},
            }
        ],
        world_cup_data=_world_cup_data(),
        normalizer=_normalizer(),
        fetched_at_epoch=1.0,
    )

    result = snapshot.results[1]
    assert result.played is False
    assert result.home_goals is None
    assert result.away_goals is None


def test_finished_football_data_match_becomes_played() -> None:
    snapshot = build_live_score_snapshot(
        provider_matches=[
            {
                "id": 1001,
                "utcDate": "2026-06-11T19:00:00Z",
                "status": "FINISHED",
                "stage": "GROUP_STAGE",
                "homeTeam": {"name": "Mexico"},
                "awayTeam": {"name": "South Africa"},
                "score": {"fullTime": {"home": 2, "away": 1}},
            }
        ],
        world_cup_data=_world_cup_data(),
        normalizer=_normalizer(),
        fetched_at_epoch=1.0,
    )

    result = snapshot.results[1]
    assert result.played is True
    assert result.status == "completed"
    assert result.home_team == "Mexico"
    assert result.away_team == "South Africa"
    assert result.home_goals == 2
    assert result.away_goals == 1


def test_finished_match_with_missing_score_is_not_played() -> None:
    snapshot = build_live_score_snapshot(
        provider_matches=[
            {
                "id": 1001,
                "utcDate": "2026-06-11T19:00:00Z",
                "status": "FINISHED",
                "stage": "GROUP_STAGE",
                "homeTeam": {"name": "Mexico"},
                "awayTeam": {"name": "South Africa"},
                "score": {"fullTime": {"home": None, "away": None}},
            }
        ],
        world_cup_data=_world_cup_data(),
        normalizer=_normalizer(),
        fetched_at_epoch=1.0,
    )

    assert snapshot.results[1].played is False


def test_non_dict_score_payload_is_treated_as_missing_score() -> None:
    snapshot = build_live_score_snapshot(
        provider_matches=[
            {
                "id": 1001,
                "utcDate": "2026-06-11T19:00:00Z",
                "status": "FINISHED",
                "stage": "GROUP_STAGE",
                "homeTeam": {"name": "Mexico"},
                "awayTeam": {"name": "South Africa"},
                "score": [],
            }
        ],
        world_cup_data=_world_cup_data(),
        normalizer=_normalizer(),
        fetched_at_epoch=1.0,
    )

    assert snapshot.results[1].played is False
    assert snapshot.results[1].home_goals is None
    assert snapshot.results[1].away_goals is None


def test_provider_team_aliases_normalize_to_local_names() -> None:
    snapshot = build_live_score_snapshot(
        provider_matches=[
            {
                "id": 1002,
                "utcDate": "2026-06-12T19:00:00Z",
                "status": "FINISHED",
                "stage": "GROUP_STAGE",
                "homeTeam": {"name": "Korea Republic"},
                "awayTeam": {"name": "Germany"},
                "score": {"fullTime": {"home": 0, "away": 3}},
            }
        ],
        world_cup_data=_world_cup_data(),
        normalizer=_normalizer(),
        fetched_at_epoch=1.0,
    )

    assert snapshot.results[2].home_team == "South Korea"
    assert snapshot.results[2].away_team == "Germany"


def test_cape_verde_islands_alias_matches_local_fixture() -> None:
    snapshot = build_live_score_snapshot(
        provider_matches=[
            {
                "id": 1003,
                "utcDate": "2026-06-13T19:00:00Z",
                "status": "FINISHED",
                "stage": "GROUP_STAGE",
                "homeTeam": {"name": "Spain"},
                "awayTeam": {"name": "Cape Verde Islands"},
                "score": {"fullTime": {"home": 1, "away": 1}},
            }
        ],
        world_cup_data={
            "schedule": [
                {
                    "match_number": 3,
                    "date": "Saturday 13 June 2026",
                    "stage": "group_stage",
                    "matchup": "Spain vs Cape Verde",
                }
            ]
        },
        normalizer=TeamNameNormalizer.build(canonical_names=["Spain", "Cape Verde"]),
        fetched_at_epoch=1.0,
    )

    assert snapshot.unmatched_provider_matches == []
    result = snapshot.results[3]
    assert result.played is True
    assert result.home_team == "Spain"
    assert result.away_team == "Cape Verde"
    assert result.home_goals == 1
    assert result.away_goals == 1


def test_unmatched_provider_rows_are_reported_not_guessed() -> None:
    snapshot = build_live_score_snapshot(
        provider_matches=[
            {
                "id": 9999,
                "utcDate": "2026-06-11T19:00:00Z",
                "status": "FINISHED",
                "stage": "GROUP_STAGE",
                "homeTeam": {"name": "Unknown FC"},
                "awayTeam": {"name": "South Africa"},
                "score": {"fullTime": {"home": 1, "away": 1}},
            }
        ],
        world_cup_data=_world_cup_data(),
        normalizer=_normalizer(),
        fetched_at_epoch=1.0,
    )

    assert snapshot.results == {}
    assert snapshot.unmatched_provider_matches[0]["reason"] == "team_name_not_recognized"
