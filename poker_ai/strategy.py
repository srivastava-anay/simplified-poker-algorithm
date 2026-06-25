"""Pot-odds-aware decision logic with controlled bluffing."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .cards import Card, ensure_unique
from .evaluator import EquityResult, MonteCarloEvaluator, draw_strength
from .opponents import ObservedAction, OpponentAction, OpponentTracker


class Action(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"


class GameStage(str, Enum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"


class Position(str, Enum):
    EARLY = "early"
    MIDDLE = "middle"
    LATE = "late"


EXPECTED_BOARD_CARDS = {
    GameStage.PREFLOP: 0,
    GameStage.FLOP: 3,
    GameStage.TURN: 4,
    GameStage.RIVER: 5,
}


@dataclass(frozen=True)
class GameState:
    hole_cards: tuple[Card, Card]
    community_cards: tuple[Card, ...]
    pot_size: float
    amount_to_call: float
    opponent_actions: tuple[OpponentAction, ...]
    num_opponents: int
    stage: GameStage
    can_check: bool
    position: Position = Position.MIDDLE

    def __post_init__(self) -> None:
        ensure_unique(self.hole_cards + self.community_cards)
        if len(self.hole_cards) != 2:
            raise ValueError("Exactly two hole cards are required")
        expected = EXPECTED_BOARD_CARDS[self.stage]
        if len(self.community_cards) != expected:
            raise ValueError(
                f"{self.stage.value} requires {expected} community cards, "
                f"got {len(self.community_cards)}"
            )
        if self.pot_size < 0 or self.amount_to_call < 0:
            raise ValueError("Pot and call amounts cannot be negative")
        if self.num_opponents < 1:
            raise ValueError("At least one opponent must remain in the hand")
        if self.can_check and self.amount_to_call > 0:
            raise ValueError("The bot cannot check when there is an amount to call")


@dataclass(frozen=True)
class Decision:
    action: Action
    amount: float
    win_probability: float
    pot_odds: float
    is_bluff: bool
    rationale: str
    equity_details: EquityResult


class StrategyEngine:
    """Combine estimated equity, price, table behavior, and restrained bluffing."""

    def __init__(
        self,
        evaluator: MonteCarloEvaluator | None = None,
        tracker: OpponentTracker | None = None,
        seed: int | None = None,
    ) -> None:
        self.evaluator = evaluator or MonteCarloEvaluator(seed=seed)
        self.tracker = tracker or OpponentTracker()
        self._rng = random.Random(seed)

    def decide(self, state: GameState) -> Decision:
        equity = self.evaluator.estimate(
            state.hole_cards, state.community_cards, state.num_opponents
        )
        pot_odds = self._pot_odds(state.pot_size, state.amount_to_call)
        edge = equity.equity - pot_odds
        aggression, weakness = self._behavior_signals(state.opponent_actions)
        draw = draw_strength(state.hole_cards, state.community_cards)
        scary = self._board_scariness(state.community_cards)

        strong_threshold = max(0.58, pot_odds + 0.16)
        very_strong = equity.equity >= max(0.72, pot_odds + 0.28)
        strong = equity.equity >= strong_threshold
        close_to_price = abs(edge) <= 0.055

        bluff_probability = self._bluff_probability(
            state=state,
            equity=equity.equity,
            aggression=aggression,
            weakness=weakness,
            draw=draw,
            scary=scary,
        )
        bluff = (
            not strong
            and equity.equity < 0.55
            and self._rng.random() < bluff_probability
        )

        if state.amount_to_call > 0:
            if bluff:
                amount = self._raise_size(state, strength=0.50 + draw * 0.20)
                return self._decision(
                    Action.RAISE, amount, equity, pot_odds, True,
                    f"Controlled semi-bluff ({bluff_probability:.0%} trigger chance) "
                    f"supported by table weakness and draw/board pressure.",
                )
            if strong and edge >= 0.10:
                amount = self._raise_size(
                    state, strength=0.72 if very_strong else 0.58
                )
                return self._decision(
                    Action.RAISE, amount, equity, pot_odds, False,
                    "Equity is comfortably above the price; raise for value.",
                )
            if edge < -0.055:
                return self._decision(
                    Action.FOLD, 0.0, equity, pot_odds, False,
                    "Estimated equity is materially below the pot odds.",
                )
            if close_to_price or edge > -0.055:
                return self._decision(
                    Action.CALL, state.amount_to_call, equity, pot_odds, False,
                    "Estimated equity is close to or above the required pot odds.",
                )

        if bluff:
            amount = self._bet_size(state, strength=0.48 + draw * 0.18)
            return self._decision(
                Action.BET, amount, equity, pot_odds, True,
                f"Controlled bluff ({bluff_probability:.0%} trigger chance) into "
                "a favorable pressure spot.",
            )
        if strong:
            amount = self._bet_size(state, strength=0.76 if very_strong else 0.58)
            return self._decision(
                Action.BET, amount, equity, pot_odds, False,
                "Strong estimated equity supports a value bet.",
            )
        if state.can_check:
            return self._decision(
                Action.CHECK, 0.0, equity, pot_odds, False,
                "No profitable value bet or controlled bluff was identified.",
            )
        return self._decision(
            Action.FOLD, 0.0, equity, pot_odds, False,
            "No free check is available and the hand lacks sufficient equity.",
        )

    def _behavior_signals(
        self, current_actions: Sequence[OpponentAction]
    ) -> tuple[float, float]:
        historic_aggression = self.tracker.table_aggression()
        historic_weakness = self.tracker.table_weakness()
        if not current_actions:
            return historic_aggression, historic_weakness

        aggressive = sum(
            event.action in {ObservedAction.BET, ObservedAction.RAISE}
            for event in current_actions
        )
        weak = sum(
            event.action in {ObservedAction.CHECK, ObservedAction.FOLD}
            for event in current_actions
        )
        count = len(current_actions)
        return (
            (historic_aggression + aggressive / count) / 2,
            (historic_weakness + weak / count) / 2,
        )

    def _bluff_probability(
        self,
        state: GameState,
        equity: float,
        aggression: float,
        weakness: float,
        draw: float,
        scary: float,
    ) -> float:
        chance = 0.015
        if state.num_opponents == 1:
            chance += 0.075
        else:
            chance -= 0.035 * (state.num_opponents - 1)
        chance += 0.10 * weakness
        chance -= 0.12 * aggression
        chance += {Position.EARLY: -0.025, Position.MIDDLE: 0.0, Position.LATE: 0.05}[
            state.position
        ]
        chance += 0.055 * scary
        chance += 0.10 * draw
        if draw < 0.12 and equity < 0.25:
            chance -= 0.05
        if state.pot_size > 0:
            chance -= 0.12 * min(state.amount_to_call / state.pot_size, 1.0)
        if any(event.action == ObservedAction.RAISE for event in state.opponent_actions):
            chance -= 0.06
        return max(0.0, min(chance, 0.22))

    @staticmethod
    def _pot_odds(pot_size: float, amount_to_call: float) -> float:
        if amount_to_call <= 0:
            return 0.0
        return amount_to_call / (pot_size + amount_to_call)

    @staticmethod
    def _board_scariness(community_cards: Sequence[Card]) -> float:
        if len(community_cards) < 3:
            return 0.0
        suits = Counter(card.suit for card in community_cards)
        ranks = sorted({card.rank_value for card in community_cards})
        score = 0.0
        if max(suits.values()) >= 3:
            score += 0.40
        if len(ranks) >= 3 and max(
            ranks[index + 2] - ranks[index] <= 4
            for index in range(len(ranks) - 2)
        ):
            score += 0.35
        if any(count > 1 for count in Counter(card.rank for card in community_cards).values()):
            score += 0.20
        score += 0.05 * sum(card.rank_value >= 11 for card in community_cards)
        return min(score, 1.0)

    @staticmethod
    def _bet_size(state: GameState, strength: float) -> float:
        fraction = min(max(strength, 0.45), 0.90)
        return StrategyEngine._chips(max(state.pot_size * fraction, 1.0))

    @staticmethod
    def _raise_size(state: GameState, strength: float) -> float:
        pot_after_call = state.pot_size + state.amount_to_call
        raise_increment = max(
            pot_after_call * min(max(strength, 0.50), 0.95),
            state.amount_to_call,
            1.0,
        )
        return StrategyEngine._chips(state.amount_to_call + raise_increment)

    @staticmethod
    def _chips(amount: float) -> float:
        return round(amount, 2)

    @staticmethod
    def _decision(
        action: Action,
        amount: float,
        equity: EquityResult,
        pot_odds: float,
        is_bluff: bool,
        rationale: str,
    ) -> Decision:
        return Decision(
            action=action,
            amount=round(amount, 2),
            win_probability=equity.equity,
            pot_odds=pot_odds,
            is_bluff=is_bluff,
            rationale=rationale,
            equity_details=equity,
        )

