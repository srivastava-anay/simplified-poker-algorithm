"""Simple opponent action history and behavior summaries."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class ObservedAction(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"


@dataclass(frozen=True)
class OpponentAction:
    player_id: str
    action: ObservedAction
    amount: float = 0.0
    faced_bet: bool = False
    faced_raise: bool = False
    pot_before_action: float = 0.0
    street: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.action, ObservedAction):
            object.__setattr__(self, "action", ObservedAction(self.action))
        if self.amount < 0 or self.pot_before_action < 0:
            raise ValueError("Action amount cannot be negative")


@dataclass
class OpponentProfile:
    hands_seen: int = 0
    folds: int = 0
    checks: int = 0
    calls: int = 0
    bets: int = 0
    raises: int = 0
    aggressive_chips: float = 0.0
    faced_bets: int = 0
    folded_to_bets: int = 0
    faced_raises: int = 0
    folded_to_raises: int = 0
    street_actions: dict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    street_faced_bets: Counter[str] = field(default_factory=Counter)
    street_folds_to_bets: Counter[str] = field(default_factory=Counter)

    @property
    def actions_seen(self) -> int:
        return self.folds + self.checks + self.calls + self.bets + self.raises

    @property
    def aggression(self) -> float:
        aggressive = self.bets + (1.5 * self.raises)
        passive = self.checks + self.calls + aggressive
        return aggressive / passive if passive else 0.0

    @property
    def weakness(self) -> float:
        weak = self.folds + self.checks
        total = self.actions_seen
        return weak / total if total else 0.0

    @property
    def fold_to_bet(self) -> float:
        # A small prior avoids wild adaptation after one observed hand.
        return (self.folded_to_bets + 1.5) / (self.faced_bets + 4.0)

    @property
    def fold_to_raise(self) -> float:
        return (self.folded_to_raises + 1.25) / (self.faced_raises + 4.0)

    @property
    def voluntary_action_rate(self) -> float:
        voluntary = self.calls + self.bets + self.raises
        meaningful = voluntary + self.folds
        return (voluntary + 2.0) / (meaningful + 5.0)

    @property
    def average_aggressive_bet(self) -> float:
        count = self.bets + self.raises
        return self.aggressive_chips / count if count else 0.0

    def street_aggression(self, street: str) -> float:
        actions = self.street_actions.get(street, Counter())
        aggressive = actions["bet"] + (1.5 * actions["raise"])
        passive = actions["check"] + actions["call"] + aggressive
        return aggressive / passive if passive else self.aggression

    def street_fold_to_bet(self, street: str) -> float:
        faced = self.street_faced_bets[street]
        folded = self.street_folds_to_bets[street]
        return (folded + 1.5) / (faced + 4.0)


class OpponentTracker:
    """Accumulate lightweight tendencies without requiring a database."""

    def __init__(self) -> None:
        self._profiles: dict[str, OpponentProfile] = defaultdict(OpponentProfile)

    def record(self, event: OpponentAction) -> None:
        profile = self._profiles[event.player_id]
        field = f"{event.action.value}s"
        setattr(profile, field, getattr(profile, field) + 1)
        if event.street:
            profile.street_actions[event.street][event.action.value] += 1
        if event.action in {ObservedAction.BET, ObservedAction.RAISE}:
            profile.aggressive_chips += event.amount
        if event.faced_bet:
            profile.faced_bets += 1
            if event.street:
                profile.street_faced_bets[event.street] += 1
            if event.action == ObservedAction.FOLD:
                profile.folded_to_bets += 1
                if event.street:
                    profile.street_folds_to_bets[event.street] += 1
        if event.faced_raise:
            profile.faced_raises += 1
            if event.action == ObservedAction.FOLD:
                profile.folded_to_raises += 1

    def record_many(self, events: Iterable[OpponentAction]) -> None:
        for event in events:
            self.record(event)

    def mark_hand_seen(self, player_id: str) -> None:
        self._profiles[player_id].hands_seen += 1

    def profile(self, player_id: str) -> OpponentProfile:
        return self._profiles[player_id]

    def table_aggression(self, active_player_ids: Iterable[str] | None = None) -> float:
        profiles = self._selected_profiles(active_player_ids)
        return sum(profile.aggression for profile in profiles) / len(profiles) if profiles else 0.0

    def table_weakness(self, active_player_ids: Iterable[str] | None = None) -> float:
        profiles = self._selected_profiles(active_player_ids)
        return sum(profile.weakness for profile in profiles) / len(profiles) if profiles else 0.0

    def estimated_fold_probability(
        self,
        active_player_ids: Iterable[str] | None = None,
        facing_raise: bool = False,
        street: str = "",
    ) -> float:
        profiles = self._selected_profiles(active_player_ids)
        if not profiles:
            return 0.36 if facing_raise else 0.32
        values = []
        for profile in profiles:
            if street and not facing_raise:
                values.append(profile.street_fold_to_bet(street))
            else:
                values.append(
                    profile.fold_to_raise if facing_raise else profile.fold_to_bet
                )
        return sum(values) / len(values)

    def table_street_aggression(
        self,
        street: str,
        active_player_ids: Iterable[str] | None = None,
    ) -> float:
        profiles = self._selected_profiles(active_player_ids)
        if not profiles:
            return 0.0
        return sum(
            profile.street_aggression(street) for profile in profiles
        ) / len(profiles)

    def _selected_profiles(
        self, active_player_ids: Iterable[str] | None
    ) -> list[OpponentProfile]:
        if active_player_ids is None:
            return list(self._profiles.values())
        return [self._profiles[player_id] for player_id in active_player_ids]
