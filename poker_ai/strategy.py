"""Pi-friendly poker strategy: charts, weighted equity, EV, and adaptation."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .cards import Card, ensure_unique
from .evaluator import (
    EquityResult,
    MonteCarloEvaluator,
    draw_strength,
    starting_hand_strength,
)
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


class Personality(str, Enum):
    BALANCED = "balanced"
    TIGHT_AGGRESSIVE = "tight-aggressive"
    LOOSE_AGGRESSIVE = "loose-aggressive"
    TRICKY = "tricky"


@dataclass(frozen=True)
class PersonalitySettings:
    looseness: float
    aggression: float
    bluff_multiplier: float
    size_multiplier: float
    risk_tolerance: float


PERSONALITIES = {
    Personality.BALANCED: PersonalitySettings(0.0, 0.0, 1.0, 1.0, 0.0),
    Personality.TIGHT_AGGRESSIVE: PersonalitySettings(-0.08, 0.08, 0.75, 1.05, -0.015),
    Personality.LOOSE_AGGRESSIVE: PersonalitySettings(0.10, 0.12, 1.25, 1.08, 0.025),
    Personality.TRICKY: PersonalitySettings(0.03, 0.02, 1.35, 0.96, 0.01),
}


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
    active_opponent_ids: tuple[str, ...] = ()
    effective_stack: float | None = None
    big_blind: float = 1.0

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
        if self.big_blind <= 0:
            raise ValueError("big_blind must be positive")


@dataclass(frozen=True)
class Decision:
    action: Action
    amount: float
    win_probability: float
    pot_odds: float
    is_bluff: bool
    rationale: str
    equity_details: EquityResult


@dataclass(frozen=True)
class BoardTexture:
    wetness: float
    high_cards: int
    paired: bool
    monotone: bool


class StrategyEngine:
    """Fast adaptive strategy designed for small Linux boards."""

    def __init__(
        self,
        evaluator: MonteCarloEvaluator | None = None,
        tracker: OpponentTracker | None = None,
        seed: int | None = None,
        personality: Personality | str = Personality.BALANCED,
    ) -> None:
        self.evaluator = evaluator or MonteCarloEvaluator(seed=seed)
        self.tracker = tracker or OpponentTracker()
        self._rng = random.Random(seed)
        self.personality = Personality(personality)
        self.settings = PERSONALITIES[self.personality]

    def decide(self, state: GameState) -> Decision:
        aggression, weakness = self._behavior_signals(state.opponent_actions)
        range_strengths = self._opponent_range_strengths(state)
        simulations = self._simulation_budget(state)
        equity = self._estimate_equity(state, simulations, range_strengths)
        pot_odds = self._pot_odds(state.pot_size, state.amount_to_call)
        texture = self._board_texture(state.community_cards)
        draw = draw_strength(state.hole_cards, state.community_cards)
        fold_equity = self._fold_equity(state, aggression, weakness)

        if state.stage == GameStage.PREFLOP:
            return self._decide_preflop(state, equity, pot_odds, fold_equity)
        return self._decide_postflop(
            state, equity, pot_odds, fold_equity, draw, texture
        )

    def _decide_preflop(
        self,
        state: GameState,
        equity: EquityResult,
        pot_odds: float,
        fold_equity: float,
    ) -> Decision:
        score = starting_hand_strength(state.hole_cards)
        position_threshold = {
            Position.EARLY: 0.63,
            Position.MIDDLE: 0.55,
            Position.LATE: 0.46,
        }[state.position]
        threshold = (
            position_threshold
            + min(0.14, 0.035 * (state.num_opponents - 1))
            - self.settings.looseness
        )
        facing_raise = any(
            action.action == ObservedAction.RAISE for action in state.opponent_actions
        )
        price_pressure = (
            min(state.amount_to_call / max(state.pot_size, 1.0), 1.0)
            if state.amount_to_call
            else 0.0
        )
        if state.amount_to_call:
            threshold += 0.03 + (0.05 * price_pressure) + (0.035 if facing_raise else 0.0)

        premium = score >= max(0.78, threshold + 0.15)
        playable = score >= threshold
        marginal = score >= threshold - 0.09
        call_ev = self._call_ev(
            equity.equity, state.pot_size, state.amount_to_call
        )

        if state.amount_to_call:
            if premium:
                amount = self._raise_size(
                    state, 0.72 * self.settings.size_multiplier
                )
                return self._decision(
                    Action.RAISE, amount, equity, pot_odds, False,
                    "Premium preflop chart hand; raise for value.",
                )
            if playable and call_ev >= -state.big_blind * 0.25:
                if (
                    score >= threshold + 0.07
                    and fold_equity >= 0.30
                    and self._rng.random() < 0.45 + self.settings.aggression
                ):
                    amount = self._raise_size(
                        state, 0.58 * self.settings.size_multiplier
                    )
                    return self._decision(
                        Action.RAISE, amount, equity, pot_odds, False,
                        "Strong chart hand with useful fold equity.",
                    )
                return self._decision(
                    Action.CALL, state.amount_to_call, equity, pot_odds, False,
                    "Preflop chart and price support a call.",
                )
            if marginal and call_ev >= -state.big_blind * (
                0.4 + self.settings.risk_tolerance
            ):
                return self._decision(
                    Action.CALL, state.amount_to_call, equity, pot_odds, False,
                    "Marginal chart hand is receiving a sufficiently cheap price.",
                )
            return self._decision(
                Action.FOLD, 0.0, equity, pot_odds, False,
                "Hand falls below the position-aware preflop continuing range.",
            )

        if premium or playable:
            strength = 0.72 if premium else 0.52 + (score - threshold) * 0.7
            amount = self._bet_size(
                state, strength * self.settings.size_multiplier, preflop=True
            )
            return self._decision(
                Action.BET, amount, equity, pot_odds, False,
                "Position-aware preflop chart recommends opening.",
            )
        if state.can_check:
            return self._decision(
                Action.CHECK, 0.0, equity, pot_odds, False,
                "Free option retained with a hand outside the opening range.",
            )
        return self._decision(
            Action.FOLD, 0.0, equity, pot_odds, False,
            "Hand is outside the preflop opening range.",
        )

    def _decide_postflop(
        self,
        state: GameState,
        equity: EquityResult,
        pot_odds: float,
        fold_equity: float,
        draw: float,
        texture: BoardTexture,
    ) -> Decision:
        fair_share = 1.0 / (state.num_opponents + 1)
        value_threshold = min(
            0.66,
            max(
                fair_share + 0.15,
                0.52 if state.num_opponents == 1 else 0.38,
            ),
        )
        value_threshold -= self.settings.aggression * 0.12
        strong = equity.equity >= value_threshold
        very_strong = equity.equity >= min(0.85, value_threshold + 0.18)
        call_ev = self._call_ev(
            equity.equity, state.pot_size, state.amount_to_call
        )
        candidates = self._candidate_bets(state, texture, very_strong)
        aggressive_options = [
            (
                amount,
                self._aggressive_ev(
                    equity.equity,
                    state.pot_size,
                    amount,
                    fold_equity,
                    state.num_opponents,
                ),
            )
            for amount in candidates
        ]
        best_amount, best_raise_ev = max(
            aggressive_options, key=lambda item: item[1]
        )
        bluff_support = (
            draw >= 0.20
            or texture.wetness >= 0.55
            or (texture.paired and state.num_opponents == 1)
        )
        bluff_frequency = min(
            0.22,
            (0.04 + 0.18 * fold_equity + 0.08 * draw)
            * self.settings.bluff_multiplier
            / max(state.num_opponents, 1),
        )
        semi_bluff = (
            not strong
            and bluff_support
            and best_raise_ev > max(0.0, call_ev) + state.big_blind * 0.15
            and self._rng.random() < bluff_frequency
        )

        if state.amount_to_call:
            if strong and best_raise_ev > call_ev + state.big_blind * 0.4:
                return self._decision(
                    Action.RAISE, best_amount, equity, pot_odds, False,
                    "Value raise has higher estimated EV than calling.",
                )
            if semi_bluff:
                return self._decision(
                    Action.RAISE, best_amount, equity, pot_odds, True,
                    "Draw, blockers, and fold equity support a controlled semi-bluff.",
                )
            tolerance = state.big_blind * (
                0.12 + self.settings.risk_tolerance
            )
            if call_ev >= -tolerance:
                return self._decision(
                    Action.CALL, state.amount_to_call, equity, pot_odds, False,
                    "Calling has non-negative or acceptably close estimated EV.",
                )
            return self._decision(
                Action.FOLD, 0.0, equity, pot_odds, False,
                "Calling has materially negative estimated EV.",
            )

        if strong and best_raise_ev > state.big_blind * 0.1:
            return self._decision(
                Action.BET, best_amount, equity, pot_odds, False,
                "Best candidate size produces positive value-bet EV.",
            )
        if semi_bluff:
            return self._decision(
                Action.BET, best_amount, equity, pot_odds, True,
                "Controlled semi-bluff is supported by fold and draw equity.",
            )
        return self._decision(
            Action.CHECK if state.can_check else Action.FOLD,
            0.0,
            equity,
            pot_odds,
            False,
            "Checking preserves equity when no candidate bet clears the EV threshold.",
        )

    def _estimate_equity(
        self,
        state: GameState,
        simulations: int,
        range_strengths: tuple[float, ...],
    ) -> EquityResult:
        try:
            return self.evaluator.estimate(
                state.hole_cards,
                state.community_cards,
                state.num_opponents,
                simulations=simulations,
                range_strengths=range_strengths,
            )
        except TypeError:
            # Keeps simple test doubles and third-party evaluators compatible.
            return self.evaluator.estimate(
                state.hole_cards, state.community_cards, state.num_opponents
            )

    def _simulation_budget(self, state: GameState) -> int:
        maximum = max(int(getattr(self.evaluator, "simulations", 1_000)), 1)
        street_factor = {
            GameStage.PREFLOP: 0.28,
            GameStage.FLOP: 1.0,
            GameStage.TURN: 0.78,
            GameStage.RIVER: 0.52,
        }[state.stage]
        opponent_factor = 1.0 / (1.0 + 0.10 * (state.num_opponents - 1))
        pressure_factor = 1.12 if state.amount_to_call > state.pot_size * 0.4 else 1.0
        return min(maximum, max(120, int(maximum * street_factor * opponent_factor * pressure_factor)))

    def _opponent_range_strengths(self, state: GameState) -> tuple[float, ...]:
        by_player: dict[str, list[OpponentAction]] = defaultdict(list)
        for event in state.opponent_actions:
            by_player[event.player_id].append(event)
        player_ids = list(state.active_opponent_ids)
        if not player_ids:
            player_ids = list(by_player)

        strengths = []
        for player_id in player_ids[: state.num_opponents]:
            profile = self.tracker.profile(player_id)
            strength = 0.08 + 0.24 * (1.0 - profile.voluntary_action_rate)
            for event in by_player.get(player_id, ()):
                action_signal = {
                    ObservedAction.FOLD: -0.10,
                    ObservedAction.CHECK: -0.04,
                    ObservedAction.CALL: 0.14,
                    ObservedAction.BET: 0.28,
                    ObservedAction.RAISE: 0.48,
                }[event.action]
                if event.action in {ObservedAction.BET, ObservedAction.RAISE}:
                    action_signal *= 1.0 - (0.45 * profile.aggression)
                strength += action_signal
                if event.pot_before_action:
                    strength += 0.10 * min(
                        event.amount / event.pot_before_action, 1.5
                    )
            strengths.append(max(0.0, min(strength, 0.95)))

        observed_average = (
            sum(strengths) / len(strengths) if strengths else 0.12
        )
        while len(strengths) < state.num_opponents:
            strengths.append(observed_average)
        return tuple(strengths)

    def _fold_equity(
        self, state: GameState, aggression: float, weakness: float
    ) -> float:
        facing_raise = state.amount_to_call > 0
        ids = state.active_opponent_ids or None
        historic = self.tracker.estimated_fold_probability(ids, facing_raise)
        estimate = historic + 0.20 * weakness - 0.14 * aggression
        estimate += 0.05 if state.position == Position.LATE else 0.0
        estimate -= 0.04 * (state.num_opponents - 1)
        return max(0.08, min(estimate, 0.72))

    def _behavior_signals(
        self, current_actions: Sequence[OpponentAction]
    ) -> tuple[float, float]:
        historic_aggression = self.tracker.table_aggression()
        historic_weakness = self.tracker.table_weakness()
        if not current_actions:
            return historic_aggression, historic_weakness
        count = len(current_actions)
        aggression = sum(
            event.action in {ObservedAction.BET, ObservedAction.RAISE}
            for event in current_actions
        ) / count
        weakness = sum(
            event.action in {ObservedAction.CHECK, ObservedAction.FOLD}
            for event in current_actions
        ) / count
        return (
            (historic_aggression + aggression) / 2,
            (historic_weakness + weakness) / 2,
        )

    def _candidate_bets(
        self, state: GameState, texture: BoardTexture, very_strong: bool
    ) -> tuple[float, ...]:
        if texture.wetness < 0.28:
            fractions = (0.35, 0.55)
        elif texture.wetness < 0.62:
            fractions = (0.48, 0.70)
        else:
            fractions = (0.62, 0.88)
        if very_strong:
            fractions = (*fractions, 1.05)

        amounts = []
        for fraction in fractions:
            adjusted = fraction * self.settings.size_multiplier
            if state.amount_to_call:
                pot_after_call = state.pot_size + state.amount_to_call
                amount = state.amount_to_call + max(
                    state.amount_to_call,
                    pot_after_call * adjusted,
                )
            else:
                amount = max(state.big_blind, state.pot_size * adjusted)
            if state.effective_stack is not None:
                amount = min(amount, state.effective_stack)
            amounts.append(self._chips(max(amount, 1.0)))
        return tuple(dict.fromkeys(amounts))

    @staticmethod
    def _call_ev(equity: float, pot: float, call: float) -> float:
        return equity * (pot + call) - call

    @staticmethod
    def _aggressive_ev(
        equity: float,
        pot: float,
        investment: float,
        fold_probability: float,
        opponents: int,
    ) -> float:
        fold_all = fold_probability ** max(opponents, 1)
        called_ev = equity * (pot + (2 * investment)) - investment
        return fold_all * pot + (1.0 - fold_all) * called_ev

    @staticmethod
    def _pot_odds(pot_size: float, amount_to_call: float) -> float:
        if amount_to_call <= 0:
            return 0.0
        return amount_to_call / (pot_size + amount_to_call)

    @staticmethod
    def _board_texture(community_cards: Sequence[Card]) -> BoardTexture:
        if len(community_cards) < 3:
            return BoardTexture(0.0, 0, False, False)
        suit_counts = Counter(card.suit for card in community_cards)
        rank_counts = Counter(card.rank for card in community_cards)
        ranks = sorted({card.rank_value for card in community_cards})
        wetness = 0.0
        maximum_suit = max(suit_counts.values())
        if maximum_suit >= 3:
            wetness += 0.42
        elif maximum_suit == 2:
            wetness += 0.18
        for index in range(max(0, len(ranks) - 2)):
            if ranks[index + 2] - ranks[index] <= 4:
                wetness += 0.32
                break
        paired = any(count > 1 for count in rank_counts.values())
        if paired:
            wetness -= 0.08
        high_cards = sum(card.rank_value >= 11 for card in community_cards)
        wetness += min(0.14, high_cards * 0.045)
        return BoardTexture(
            max(0.0, min(wetness, 1.0)),
            high_cards,
            paired,
            maximum_suit == len(community_cards),
        )

    @staticmethod
    def _bet_size(
        state: GameState, strength: float, preflop: bool = False
    ) -> float:
        if preflop:
            amount = max(
                state.big_blind * (2.2 + 0.9 * strength),
                state.pot_size * 0.55,
            )
        else:
            amount = max(state.pot_size * min(max(strength, 0.35), 1.05), 1.0)
        if state.effective_stack is not None:
            amount = min(amount, state.effective_stack)
        return StrategyEngine._chips(amount)

    @staticmethod
    def _raise_size(state: GameState, strength: float) -> float:
        pot_after_call = state.pot_size + state.amount_to_call
        increment = max(
            state.amount_to_call,
            pot_after_call * min(max(strength, 0.45), 1.0),
        )
        amount = state.amount_to_call + increment
        if state.effective_stack is not None:
            amount = min(amount, state.effective_stack)
        return StrategyEngine._chips(amount)

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
