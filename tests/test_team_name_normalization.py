from __future__ import annotations

import pytest

from src.team_name_normalization import TeamNameNormalizer, normalize_team_name


def test_normalize_team_name_removes_diacritics_and_punctuation() -> None:
    assert normalize_team_name("Curaçao") == "curacao"
    assert normalize_team_name("Bosnia-Herzegovina") == "bosnia herzegovina"
    assert normalize_team_name("Cote d'Ivoire") == "cote d ivoire"


def test_team_name_normalizer_resolves_polymarket_aliases() -> None:
    normalizer = TeamNameNormalizer.build(
        canonical_names=[
            "Turkey",
            "Iran",
            "South Korea",
            "Czech Republic",
            "Ivory Coast",
            "Curaçao",
            "Cape Verde",
            "DR Congo",
            "Bosnia and Herzegovina",
        ]
    )

    assert normalizer.resolve("Turkiye") == "Turkey"
    assert normalizer.resolve("IR Iran") == "Iran"
    assert normalizer.resolve("Korea Republic") == "South Korea"
    assert normalizer.resolve("Czechia") == "Czech Republic"
    assert normalizer.resolve("Cote d'Ivoire") == "Ivory Coast"
    assert normalizer.resolve("Curacao") == "Curaçao"
    assert normalizer.resolve("Cabo Verde") == "Cape Verde"
    assert normalizer.resolve("Cape Verde Islands") == "Cape Verde"
    assert normalizer.resolve("Congo DR") == "DR Congo"
    assert normalizer.resolve("Bosnia-Herzegovina") == "Bosnia and Herzegovina"


def test_team_name_normalizer_rejects_ambiguous_aliases() -> None:
    with pytest.raises(ValueError, match="Ambiguous team normalization key"):
        TeamNameNormalizer.build(
            canonical_names=["Congo", "DR Congo"],
            alias_map={"congo dr": "Congo", "Congo DR": "DR Congo"},
        )
