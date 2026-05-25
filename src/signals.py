from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SignalContext:
    match_id: str
    home_slot_team: str
    away_slot_team: str


class SignalProvider(Protocol):
    def score(self, context: SignalContext) -> float:
        ...


class GroupFormSignal:
    """Proxy for recent group-stage form and points trend."""

    _seed = {
        "USA": 0.68,
        "Mexico": 0.63,
        "Germany": 0.74,
        "Brazil": 0.81,
        "England": 0.77,
        "Netherlands": 0.72,
        "Japan": 0.61,
        "Spain": 0.79,
        "TBD": 0.50,
    }

    def score(self, context: SignalContext) -> float:
        a = self._seed.get(context.home_slot_team, 0.5)
        b = self._seed.get(context.away_slot_team, 0.5)
        return (a + b) / 2


class EloStrengthSignal:
    """Proxy for pre-tournament and live-updated strength ratings."""

    _seed = {
        "USA": 0.55,
        "Mexico": 0.57,
        "Germany": 0.80,
        "Brazil": 0.86,
        "England": 0.82,
        "Netherlands": 0.78,
        "Japan": 0.62,
        "Spain": 0.84,
        "TBD": 0.50,
    }

    def score(self, context: SignalContext) -> float:
        a = self._seed.get(context.home_slot_team, 0.5)
        b = self._seed.get(context.away_slot_team, 0.5)
        return (a + b) / 2


class TravelRestSignal:
    """Proxy for rest days, travel distance, and climate adaptation."""

    def score(self, context: SignalContext) -> float:
        # Placeholder deterministic signal. Replace with real schedule/travel logic.
        combined = f"{context.home_slot_team}:{context.away_slot_team}:{context.match_id}"
        return 0.45 + (sum(ord(c) for c in combined) % 30) / 100
