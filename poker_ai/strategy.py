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

# Keeps the richer strategy while reducing marginal aggression by roughly half.
AGGRESSION_DIAL = 0.50


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
    hero_actions: tuple[OpponentAction, ...] = ()

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
    flush_pressure: float = 0.0
    straight_pressure: float = 0.0


@dataclass(frozen=True)
class BluffSignals:
    draw_equity: float
    blocker_strength: float
    missed_draw: float
    scare_card: float
    opponent_weakness: float

    @property
    def total(self) -> float:
        return min(
            1.0,
            0.25 * self.draw_equity
            + 0.25 * self.blocker_strength
            + 0.18 * self.missed_draw
            + 0.17 * self.scare_card
            + 0.15 * self.opponent_weakness,
        )


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
        daring = self._daring_factor(state)
        bluff_signals = self._bluff_signals(state, draw, weakness, texture)

        if state.stage == GameStage.PREFLOP:
            return self._decide_preflop(
                state, equity, pot_odds, fold_equity, daring
            )
        return self._decide_postflop(
            state,
            equity,
            pot_odds,
            fold_equity,
            draw,
            texture,
            bluff_signals,
            daring,
            aggression,
        )

    def _decide_preflop(
        self,
        state: GameState,
        equity: EquityResult,
        pot_odds: float,
        fold_equity: float,
        daring: float,
    ) -> Decision:
        score = starting_hand_strength(state.hole_cards)
        position_threshold = {
            Position.EARLY: 0.59,
            Position.MIDDLE: 0.51,
            Position.LATE: 0.42,
        }[state.position]
        threshold = (
            position_threshold
            + min(0.11, 0.028 * (state.num_opponents - 1))
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
            threshold += 0.015 + (0.035 * price_pressure) + (0.025 if facing_raise else 0.0)

        premium = score >= max(0.78, threshold + 0.15)
        playable = score >= threshold
        marginal = score >= threshold - 0.13
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
            if playable and call_ev >= -state.big_blind * 0.55:
                calls_before = sum(
                    action.action == ObservedAction.CALL
                    for action in state.opponent_actions
                )
                squeeze_bonus = min(0.16, calls_before * 0.055)
                if (
                    score >= threshold + 0.07 - squeeze_bonus
                    and fold_equity >= 0.26
                    and self._rng.random()
                    < (
                        0.43 + self.settings.aggression + 0.16 * daring
                    )
                    * AGGRESSION_DIAL
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
            cheap_call = state.amount_to_call <= max(
                state.big_blind, state.pot_size * 0.18
            )
            if marginal and (
                call_ev >= -state.big_blind * (
                    0.75 + self.settings.risk_tolerance
                )
                or cheap_call
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
            if (
                state.position == Position.LATE
                and fold_equity >= 0.42
                and score >= threshold - 0.05
            ):
                strength += 0.08 * daring * AGGRESSION_DIAL
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
        bluff_signals: BluffSignals,
        daring: float,
        opponent_aggression: float,
    ) -> Decision:
        fair_share = 1.0 / (state.num_opponents + 1)
        value_threshold = min(
            0.66,
            max(
                fair_share + 0.15,
                0.52 if state.num_opponents == 1 else 0.38,
            ),
        )
        calling_station = max(0.0, 0.38 - fold_equity)
        value_threshold -= (
            self.settings.aggression * 0.12
            + 0.10 * calling_station
            + 0.025 * daring * AGGRESSION_DIAL
        )
        strong = equity.equity >= value_threshold
        very_strong = equity.equity >= min(0.85, value_threshold + 0.18)
        medium_strength = equity.equity >= max(
            pot_odds + 0.05,
            value_threshold - 0.16,
        )
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
        bluff_support = bluff_signals.total >= 0.16
        base_bluff_frequency = (
            0.035
            + 0.22 * fold_equity
            + 0.20 * bluff_signals.total
            + 0.055 * daring
        )
        if state.stage == GameStage.RIVER:
            base_bluff_frequency += (
                0.08 * bluff_signals.blocker_strength
                + 0.06 * bluff_signals.missed_draw
            )
        opponent_penalty = max(1.0, state.num_opponents ** 1.35)
        bluff_cap = 0.38 if state.num_opponents == 1 else 0.14
        bluff_frequency = min(
            bluff_cap,
            base_bluff_frequency
            * self.settings.bluff_multiplier
            * AGGRESSION_DIAL
            / opponent_penalty,
        )
        bluff_ev_hurdle = state.big_blind * (
            0.20 - 0.08 * daring * AGGRESSION_DIAL
        )
        aggressive_bluff = (
            not strong
            and bluff_support
            and best_raise_ev > max(0.0, call_ev) + bluff_ev_hurdle
            and self._rng.random() < bluff_frequency
        )
        bluff_amount = self._bluff_size(
            state, texture, bluff_signals, daring
        )

        if state.amount_to_call:
            value_raise = (
                very_strong
                or self._rng.random()
                < (0.38 + 0.18 * daring) * AGGRESSION_DIAL
            )
            if (
                strong
                and value_raise
                and best_raise_ev > call_ev + state.big_blind * 0.4
            ):
                return self._decision(
                    Action.RAISE, best_amount, equity, pot_odds, False,
                    "Value raise has higher estimated EV than calling.",
                )
            if aggressive_bluff:
                return self._decision(
                    Action.RAISE, max(best_amount, bluff_amount), equity, pot_odds, True,
                    "Blockers, draw history, fold equity, and controlled daring support a bluff raise.",
                )
            bluff_catcher_bonus = (
                0.018 * opponent_aggression
                if state.num_opponents == 1
                else 0.0
            )
            defense_margin = (
                0.025
                + 0.055 * draw
                + (0.018 if state.num_opponents == 1 else 0.0)
                + bluff_catcher_bonus
                + max(self.settings.risk_tolerance, 0.0)
            )
            required_equity = max(0.0, pot_odds - defense_margin)
            tolerance = max(
                state.big_blind * (0.65 + self.settings.risk_tolerance),
                state.pot_size * (0.025 + 0.035 * draw),
            )
            if equity.equity >= required_equity or call_ev >= -tolerance:
                return self._decision(
                    Action.CALL, state.amount_to_call, equity, pot_odds, False,
                    "Equity, price, or draw potential supports defending.",
                )
            return self._decision(
                Action.FOLD, 0.0, equity, pot_odds, False,
                "Calling has materially negative estimated EV.",
            )

        low_spr = self._stack_to_pot_ratio(state) <= 0.80
        slowplay = (
            very_strong
            and texture.wetness <= 0.24
            and not low_spr
            and self.personality in {Personality.BALANCED, Personality.TRICKY}
            and self._rng.random() < 0.08 + 0.12 * daring
        )
        if slowplay:
            return self._decision(
                Action.CHECK, 0.0, equity, pot_odds, False,
                "Occasional dry-board slowplay protects the checking range.",
            )
        if strong and (
            best_raise_ev > state.big_blind * 0.1 or low_spr
        ):
            value_amount = (
                max(candidates)
                if very_strong or calling_station >= 0.10
                else best_amount
            )
            return self._decision(
                Action.BET, value_amount, equity, pot_odds, False,
                "Best candidate size produces positive value-bet EV.",
            )
        protection_bet = (
            state.stage in {GameStage.FLOP, GameStage.TURN}
            and medium_strength
            and texture.wetness >= 0.48
            and state.num_opponents <= 2
            and self._rng.random()
            < (0.48 + 0.18 * daring) * AGGRESSION_DIAL
        )
        if protection_bet:
            amount = self._protection_size(state, texture)
            return self._decision(
                Action.BET, amount, equity, pot_odds, False,
                "Medium-strength hand bets for protection on a dynamic board.",
            )
        if aggressive_bluff:
            return self._decision(
                Action.BET, bluff_amount, equity, pot_odds, True,
                "Blockers, missed draws, scare cards, and fold equity support a controlled bluff.",
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
            GameStage.PREFLOP: 0.22,
            GameStage.FLOP: 0.88,
            GameStage.TURN: 0.68,
            GameStage.RIVER: 0.44,
        }[state.stage]
        opponent_factor = 1.0 / (1.0 + 0.16 * (state.num_opponents - 1))
        pressure_factor = 1.10 if state.amount_to_call > state.pot_size * 0.4 else 1.0
        return min(
            maximum,
            max(
                100,
                int(
                    maximum
                    * street_factor
                    * opponent_factor
                    * pressure_factor
                ),
            ),
        )

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

    def _daring_factor(self, state: GameState) -> float:
        """Return bounded decision noise that changes appetite, not hand strength."""

        personality_bias = {
            Personality.TIGHT_AGGRESSIVE: -0.12,
            Personality.BALANCED: 0.0,
            Personality.LOOSE_AGGRESSIVE: 0.16,
            Personality.TRICKY: 0.12,
        }[self.personality]
        situational = 0.0
        if state.num_opponents == 1:
            situational += 0.10
        if state.position == Position.LATE:
            situational += 0.08
        if state.stage == GameStage.RIVER:
            situational += 0.04
        # Triangular noise clusters around sensible play but occasionally
        # creates a noticeably more daring line.
        noise = self._rng.triangular(-0.18, 0.34, 0.04)
        raw = max(0.0, min(0.5 + personality_bias + situational + noise, 1.0))
        return 0.5 + (raw - 0.5) * AGGRESSION_DIAL

    def _bluff_signals(
        self,
        state: GameState,
        draw: float,
        weakness: float,
        texture: BoardTexture,
    ) -> BluffSignals:
        blocker = self._blocker_strength(
            state.hole_cards, state.community_cards
        )
        missed_draw = self._missed_draw_strength(
            state.hole_cards, state.community_cards
        )
        scare_card = self._scare_card_strength(state.community_cards)
        current_street_actions = [
            event
            for event in state.opponent_actions
            if not event.street or event.street == state.stage.value
        ]
        if current_street_actions:
            checks = sum(
                event.action == ObservedAction.CHECK
                for event in current_street_actions
            )
            calls = sum(
                event.action == ObservedAction.CALL
                for event in current_street_actions
            )
            aggression = sum(
                event.action in {ObservedAction.BET, ObservedAction.RAISE}
                for event in current_street_actions
            )
            action_weakness = max(
                0.0,
                (0.65 * checks + 0.30 * calls - 0.8 * aggression)
                / len(current_street_actions),
            )
            weakness = max(weakness, action_weakness)
        if state.position == Position.LATE:
            weakness += 0.08
        prior_aggression = any(
            event.action in {ObservedAction.BET, ObservedAction.RAISE}
            for event in state.hero_actions
            if event.street != state.stage.value
        )
        raised_preflop = any(
            event.action == ObservedAction.RAISE
            and event.street == GameStage.PREFLOP.value
            for event in state.hero_actions
        )
        checked_flop = any(
            event.action == ObservedAction.CHECK
            and event.street == GameStage.FLOP.value
            for event in state.hero_actions
        )
        if state.stage == GameStage.FLOP and raised_preflop:
            weakness += 0.12
        if (
            state.stage == GameStage.TURN
            and checked_flop
            and current_street_actions
            and all(
                event.action == ObservedAction.CHECK
                for event in current_street_actions
            )
        ):
            # Delayed continuation bets are credible after two checks.
            weakness += 0.16
        if prior_aggression and scare_card >= 0.25:
            scare_card += 0.12
        if texture.paired and state.num_opponents == 1:
            scare_card = max(scare_card, 0.30)
        return BluffSignals(
            draw_equity=draw,
            blocker_strength=blocker,
            missed_draw=missed_draw,
            scare_card=scare_card,
            opponent_weakness=min(weakness, 1.0),
        )

    @staticmethod
    def _blocker_strength(
        hole_cards: Sequence[Card], community_cards: Sequence[Card]
    ) -> float:
        if len(community_cards) < 3:
            return 0.0
        score = 0.0
        suit_counts = Counter(card.suit for card in community_cards)
        dominant_suit, suit_count = max(
            suit_counts.items(), key=lambda item: item[1]
        )
        suited_hole = [
            card for card in hole_cards if card.suit == dominant_suit
        ]
        if suit_count >= 3 and suited_hole:
            highest = max(card.rank_value for card in suited_hole)
            if highest == 14:
                score += 0.70 if suit_count >= 4 else 0.55
            elif highest == 13:
                score += 0.42
            elif highest >= 11:
                score += 0.22

        board_ranks = {card.rank_value for card in community_cards}
        if 14 in board_ranks:
            board_ranks.add(1)
        hole_ranks = {card.rank_value for card in hole_cards}
        if 14 in hole_ranks:
            hole_ranks.add(1)
        for start in range(1, 11):
            window = set(range(start, start + 5))
            missing = window - board_ranks
            if len(missing) == 1 and missing.intersection(hole_ranks):
                score += 0.35 if start >= 6 else 0.22
        return min(score, 1.0)

    @staticmethod
    def _missed_draw_strength(
        hole_cards: Sequence[Card], community_cards: Sequence[Card]
    ) -> float:
        if len(community_cards) != 5:
            return 0.0
        turn_board = community_cards[:-1]
        river = community_cards[-1]
        turn_draw = draw_strength(hole_cards, turn_board)
        if turn_draw < 0.18:
            return 0.0

        combined_turn = tuple(hole_cards) + tuple(turn_board)
        suit_counts = Counter(card.suit for card in combined_turn)
        flush_draw_suits = {
            suit for suit, count in suit_counts.items() if count == 4
        }
        flush_missed = bool(flush_draw_suits) and river.suit not in flush_draw_suits

        turn_ranks = {card.rank_value for card in combined_turn}
        river_rank = river.rank_value
        if 14 in turn_ranks:
            turn_ranks.add(1)
        straight_missed = False
        for start in range(1, 11):
            window = set(range(start, start + 5))
            missing = window - turn_ranks
            if len(missing) == 1 and river_rank not in missing:
                straight_missed = True
                break
        return min(
            1.0,
            (0.58 if flush_missed else 0.0)
            + (0.42 if straight_missed else 0.0),
        )

    @classmethod
    def _scare_card_strength(cls, community_cards: Sequence[Card]) -> float:
        if len(community_cards) < 4:
            return 0.0
        previous = community_cards[:-1]
        latest = community_cards[-1]
        score = 0.0
        if latest.rank_value == 14:
            score += 0.38
        elif latest.rank_value == 13:
            score += 0.24

        previous_suits = Counter(card.suit for card in previous)
        current_suits = Counter(card.suit for card in community_cards)
        if previous_suits[latest.suit] == 2 and current_suits[latest.suit] == 3:
            score += 0.36
        if previous_suits[latest.suit] == 3 and current_suits[latest.suit] == 4:
            score += 0.24

        previous_straight = cls._straight_pressure(previous)
        current_straight = cls._straight_pressure(community_cards)
        if current_straight > previous_straight:
            score += 0.30
        if sum(card.rank_value == latest.rank_value for card in previous):
            score += 0.16
        return min(score, 1.0)

    @staticmethod
    def _straight_pressure(cards: Sequence[Card]) -> float:
        ranks = {card.rank_value for card in cards}
        if 14 in ranks:
            ranks.add(1)
        best = max(
            (
                len(ranks.intersection(range(start, start + 5)))
                for start in range(1, 11)
            ),
            default=0,
        )
        return max(0.0, (best - 2) / 3)

    def _candidate_bets(
        self, state: GameState, texture: BoardTexture, very_strong: bool
    ) -> tuple[float, ...]:
        if texture.wetness < 0.28:
            fractions = (0.30, 0.44)
        elif texture.wetness < 0.62:
            fractions = (0.40, 0.56)
        else:
            fractions = (0.50, 0.70)
        if very_strong:
            fractions = (*fractions, 0.86)

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

    def _bluff_size(
        self,
        state: GameState,
        texture: BoardTexture,
        signals: BluffSignals,
        daring: float,
    ) -> float:
        polarized = (
            state.stage == GameStage.RIVER
            and (
                signals.blocker_strength >= 0.35
                or signals.scare_card >= 0.45
            )
        )
        if polarized:
            fraction = 0.64 + 0.22 * daring
        elif signals.draw_equity >= 0.35:
            fraction = 0.44 + 0.14 * texture.wetness
        else:
            fraction = 0.48 + 0.12 * daring
        fraction *= self.settings.size_multiplier
        if state.amount_to_call:
            pot_after_call = state.pot_size + state.amount_to_call
            amount = state.amount_to_call + max(
                state.amount_to_call,
                pot_after_call * fraction,
            )
        else:
            amount = max(state.big_blind, state.pot_size * fraction)
        if state.effective_stack is not None:
            amount = min(amount, state.effective_stack)
        return self._chips(max(amount, 1.0))

    @staticmethod
    def _protection_size(
        state: GameState, texture: BoardTexture
    ) -> float:
        fraction = 0.32 + 0.13 * texture.wetness
        amount = max(state.big_blind, state.pot_size * fraction)
        if state.effective_stack is not None:
            amount = min(amount, state.effective_stack)
        return StrategyEngine._chips(max(amount, 1.0))

    @staticmethod
    def _stack_to_pot_ratio(state: GameState) -> float:
        if state.effective_stack is None:
            return 99.0
        return state.effective_stack / max(state.pot_size, 1.0)

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
        straight_pressure = StrategyEngine._straight_pressure(community_cards)
        return BoardTexture(
            max(0.0, min(wetness, 1.0)),
            high_cards,
            paired,
            maximum_suit == len(community_cards),
            min(maximum_suit / 4.0, 1.0),
            straight_pressure,
        )

    @staticmethod
    def _bet_size(
        state: GameState, strength: float, preflop: bool = False
    ) -> float:
        if preflop:
            amount = max(
                state.big_blind * (2.0 + 0.60 * strength),
                state.pot_size * 0.45,
            )
        else:
            amount = max(state.pot_size * min(max(strength, 0.30), 0.86), 1.0)
        if state.effective_stack is not None:
            amount = min(amount, state.effective_stack)
        return StrategyEngine._chips(amount)

    @staticmethod
    def _raise_size(state: GameState, strength: float) -> float:
        pot_after_call = state.pot_size + state.amount_to_call
        increment = max(
            state.amount_to_call,
            pot_after_call * min(max(strength * 0.78, 0.36), 0.78),
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
