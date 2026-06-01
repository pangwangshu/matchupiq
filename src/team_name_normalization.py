from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

# Polymarket team names observed to differ from local schedule naming.
DEFAULT_POLYMARKET_TEAM_ALIASES: dict[str, str] = {
    "turkiye": "Turkey",
    "ir iran": "Iran",
    "korea republic": "South Korea",
    "czechia": "Czech Republic",
    "cote d ivoire": "Ivory Coast",
    "curacao": "Curaçao",
    "cabo verde": "Cape Verde",
    "congo dr": "DR Congo",
    "bosnia herzegovina": "Bosnia and Herzegovina",
}


def normalize_team_name(raw: str) -> str:
    """Create a stable normalized key for cross-source team matching."""
    folded = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    lowered = folded.lower()
    lowered = lowered.replace("&", " and ")
    lowered = lowered.replace("'", " ")
    lowered = re.sub(r"[.\-_/]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


@dataclass(frozen=True)
class TeamNameNormalizer:
    canonical_names: set[str]
    canonical_by_normalized: dict[str, str]

    @classmethod
    def build(
        cls,
        canonical_names: Iterable[str],
        alias_map: dict[str, str] | None = None,
    ) -> "TeamNameNormalizer":
        canonical_set = {name for name in canonical_names if name}
        canonical_by_normalized: dict[str, str] = {}

        def register(key: str, canonical: str) -> None:
            existing = canonical_by_normalized.get(key)
            if existing is not None and existing != canonical:
                raise ValueError(
                    "Ambiguous team normalization key "
                    f"'{key}' maps to both '{existing}' and '{canonical}'."
                )
            canonical_by_normalized[key] = canonical

        for canonical in sorted(canonical_set):
            register(normalize_team_name(canonical), canonical)

        aliases = alias_map or DEFAULT_POLYMARKET_TEAM_ALIASES
        for raw_alias, canonical in aliases.items():
            if canonical not in canonical_set:
                continue
            register(normalize_team_name(raw_alias), canonical)

        return cls(
            canonical_names=canonical_set,
            canonical_by_normalized=canonical_by_normalized,
        )

    def resolve(self, raw_name: str) -> str | None:
        return self.canonical_by_normalized.get(normalize_team_name(raw_name))

