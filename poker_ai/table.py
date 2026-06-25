"""Reusable multiplayer Texas Hold'em table state."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from treys import Card as TreysCard
from treys import Evaluator

from .cards import Card, full_deck
from .evaluator import MonteCarloEvaluator
from .opponents import ObservedAction, OpponentAction, OpponentTracker
from .strategy import (
    Action,
    GameStage,
    GameState,
    Personality,
    Position,
    StrategyEngine,
)


@dataclass
class TablePlayer:
    player_id: str
    name: str
    is_bot: bool
    stack: int
    hole_cards: tuple[Card, Card] | None = None
    folded: bool = False
    street_contribution: int = 0
    total_contribution: int = 0

    @property
    def all_in(self) -> bool:
        return self.stack == 0 and not self.folded

    @property
    def in_hand(self) -> bool:
        return self.hole_cards is not None and not self.folded


@dataclass(frozen=True)
class TableEvent:
    text: str
    kind: str = "info"


@dataclass
class MultiplayerTable:
    """No-limit Hold'em state machine for local humans and Monte Carlo bots."""

    human_players: int
    bot_players: int
    starting_stack: int = 1_000
    small_blind: int = 5
    big_blind: int = 10
    simulations: int = 2_000
    seed: int | None = None
    players: list[TablePlayer] = field(init=False)

    def __post_init__(self) -> None:
        total = self.human_players + self.bot_players
        if self.human_players < 1:
            raise ValueError("At least one human player is required")
        if self.bot_players < 0 or not 2 <= total <= 8:
            raise ValueError("Choose between 2 and 8 total players")
        if self.starting_stack <= 0:
            raise ValueError("Starting stack must be positive")
        if not 0 < self.small_blind <= self.big_blind:
            raise ValueError("Blinds must satisfy 0 < small blind <= big blind")

        self.players = [
            TablePlayer(f"human-{i}", f"Player {i}", False, self.starting_stack)
            for i in range(1, self.human_players + 1)
        ] + [
            TablePlayer(f"bot-{i}", f"Bot {i}", True, self.starting_stack)
            for i in range(1, self.bot_players + 1)
        ]
        self._rng = random.Random(self.seed)
        self.tracker = OpponentTracker()
        personality_cycle = (
            Personality.TIGHT_AGGRESSIVE,
            Personality.LOOSE_AGGRESSIVE,
            Personality.BALANCED,
            Personality.TRICKY,
        )
        self.engines = {
            player.player_id: StrategyEngine(
                evaluator=MonteCarloEvaluator(self.simulations, self._seed_for(index)),
                tracker=self.tracker,
                seed=self._seed_for(index + 100),
                personality=personality_cycle[index % len(personality_cycle)],
            )
            for index, player in enumerate(self.players)
            if player.is_bot
        }
        self.evaluator = Evaluator()
        self.deck: list[Card] = []
        self.board: list[Card] = []
        self.events: list[TableEvent] = []
        self.street_actions: list[OpponentAction] = []
        self.hand_actions: list[OpponentAction] = []
        self.stage = GameStage.PREFLOP
        self.dealer_index = -1
        self.small_blind_index = -1
        self.big_blind_index = -1
        self.actor_index: int | None = None
        self.pending: set[int] = set()
        self.current_bet = 0
        self.minimum_raise = self.big_blind
        self.hand_number = 0
        self.hand_over = True
        self.match_over = False
        self.last_result = ""

    @property
    def pot(self) -> int:
        return sum(player.total_contribution for player in self.players)

    @property
    def actor(self) -> TablePlayer | None:
        return self.players[self.actor_index] if self.actor_index is not None else None

    @property
    def active_indices(self) -> list[int]:
        return [
            index
            for index, player in enumerate(self.players)
            if player.hole_cards is not None and not player.folded
        ]

    def start_hand(self) -> None:
        funded = [index for index, player in enumerate(self.players) if player.stack > 0]
        if len(funded) < 2:
            self.match_over = True
            self.hand_over = True
            winner = self.players[funded[0]].name if funded else "Nobody"
            self.last_result = f"{winner} wins the match."
            self._event(self.last_result, "result")
            return

        self.hand_number += 1
        self.hand_over = False
        self.last_result = ""
        self.board = []
        self.events = []
        self.street_actions = []
        self.hand_actions = []
        self.stage = GameStage.PREFLOP
        self.current_bet = 0
        self.minimum_raise = self.big_blind
        self.pending = set()
        self.deck = list(full_deck())
        self._rng.shuffle(self.deck)

        self.dealer_index = self._next_funded(self.dealer_index)
        for player in self.players:
            player.hole_cards = None
            player.folded = player.stack == 0
            player.street_contribution = 0
            player.total_contribution = 0

        for index in funded:
            self.players[index].hole_cards = (self.deck.pop(), self.deck.pop())
            self.tracker.mark_hand_seen(self.players[index].player_id)

        if len(funded) == 2:
            self.small_blind_index = self.dealer_index
            self.big_blind_index = self._next_live(self.dealer_index)
            first_actor = self.dealer_index
        else:
            self.small_blind_index = self._next_live(self.dealer_index)
            self.big_blind_index = self._next_live(self.small_blind_index)
            first_actor = self._next_live(self.big_blind_index)

        self._event(
            f"Hand {self.hand_number}. {self.players[self.dealer_index].name} has the button."
        )
        self._post_blind(self.small_blind_index, self.small_blind, "small blind")
        self._post_blind(self.big_blind_index, self.big_blind, "big blind")
        self.current_bet = max(player.street_contribution for player in self.players)
        self.pending = set(self._eligible_to_act())
        self.actor_index = self._first_pending_from(first_actor, include_start=True)
        if self.actor_index is None:
            self._runout_and_showdown()

    def legal_actions(self, index: int) -> dict[str, int | bool]:
        if self.hand_over or index != self.actor_index:
            return {}
        player = self.players[index]
        to_call = max(0, self.current_bet - player.street_contribution)
        maximum_total = player.street_contribution + player.stack
        minimum_total = (
            self.big_blind
            if self.current_bet == 0
            else self.current_bet + self.minimum_raise
        )
        can_raise = maximum_total > self.current_bet
        return {
            "to_call": to_call,
            "can_check": to_call == 0,
            "can_call": to_call > 0,
            "can_bet": self.current_bet == 0 and can_raise,
            "can_raise": self.current_bet > 0 and can_raise,
            "minimum_total": min(minimum_total, maximum_total),
            "maximum_total": maximum_total,
        }

    def act(self, index: int, action: str, amount: int | None = None) -> None:
        if index != self.actor_index or self.hand_over:
            raise ValueError("It is not that player's turn")
        player = self.players[index]
        legal = self.legal_actions(index)
        to_call = int(legal["to_call"])
        pot_before_action = self.pot
        faced_bet = to_call > 0
        faced_raise = faced_bet and any(
            event.action == ObservedAction.RAISE for event in self.street_actions
        )
        action = action.lower()
        observed: ObservedAction
        paid = 0
        raised = False

        if action == "fold":
            if to_call == 0:
                raise ValueError("Check is available for free")
            player.folded = True
            observed = ObservedAction.FOLD
            self._event(f"{player.name} folds.")
        elif action == "check":
            if to_call:
                raise ValueError(f"{to_call} chips must be called")
            observed = ObservedAction.CHECK
            self._event(f"{player.name} checks.")
        elif action == "call":
            if not to_call:
                raise ValueError("There is no bet to call")
            paid = self._pay(index, min(to_call, player.stack))
            observed = ObservedAction.CALL
            suffix = " and is all-in" if player.all_in else ""
            self._event(f"{player.name} calls {paid}{suffix}.")
        elif action in {"bet", "raise", "allin"}:
            target = player.street_contribution + player.stack if action == "allin" else amount
            if target is None:
                raise ValueError("A chip amount is required")
            maximum = int(legal["maximum_total"])
            minimum = int(legal["minimum_total"])
            if target <= self.current_bet:
                raise ValueError(f"Amount must be above {self.current_bet}")
            if target > maximum:
                raise ValueError(f"Maximum total is {maximum}")
            if target < minimum and target != maximum:
                raise ValueError(f"Minimum total is {minimum}")
            old_bet = self.current_bet
            paid = self._pay(index, target - player.street_contribution)
            self.current_bet = player.street_contribution
            increase = self.current_bet - old_bet
            if increase >= self.minimum_raise:
                self.minimum_raise = increase
            observed = ObservedAction.BET if old_bet == 0 else ObservedAction.RAISE
            verb = "bets" if observed == ObservedAction.BET else "raises to"
            suffix = " and is all-in" if player.all_in else ""
            self._event(f"{player.name} {verb} {self.current_bet}{suffix}.")
            raised = True
        else:
            raise ValueError("Unknown action")

        event = OpponentAction(
            player.player_id,
            observed,
            paid,
            faced_bet=faced_bet,
            faced_raise=faced_raise,
            pot_before_action=pot_before_action,
            street=self.stage.value,
        )
        self.street_actions.append(event)
        self.hand_actions.append(event)
        self.tracker.record(event)
        self._advance_after_action(index, raised)

    def bot_act(self) -> None:
        if self.actor_index is None or not self.players[self.actor_index].is_bot:
            raise ValueError("The current player is not a bot")
        index = self.actor_index
        player = self.players[index]
        assert player.hole_cards is not None
        legal = self.legal_actions(index)
        to_call = int(legal["to_call"])
        opponents = len(self.active_indices) - 1
        active_opponents = tuple(
            self.players[seat].player_id
            for seat in self.active_indices
            if seat != index
        )
        opponent_stacks = [
            self.players[seat].stack
            for seat in self.active_indices
            if seat != index
        ]
        state = GameState(
            hole_cards=player.hole_cards,
            community_cards=tuple(self.board),
            pot_size=self.pot,
            amount_to_call=to_call,
            opponent_actions=tuple(
                event for event in self.hand_actions if event.player_id != player.player_id
            ),
            num_opponents=max(opponents, 1),
            stage=self.stage,
            can_check=to_call == 0,
            position=self._position_for(index),
            active_opponent_ids=active_opponents,
            effective_stack=min(
                player.stack,
                max(opponent_stacks, default=player.stack),
            ),
            big_blind=self.big_blind,
        )
        decision = self.engines[player.player_id].decide(state)
        if to_call:
            if decision.action == Action.FOLD:
                self.act(index, "fold")
            elif decision.action == Action.RAISE and bool(legal["can_raise"]):
                desired = player.street_contribution + max(int(round(decision.amount)), to_call)
                self.act(index, "raise", self._legal_bot_target(legal, desired))
            else:
                self.act(index, "call")
        elif decision.action in {Action.BET, Action.RAISE} and (
            bool(legal["can_bet"]) or bool(legal["can_raise"])
        ):
            desired = player.street_contribution + int(round(decision.amount))
            action = "bet" if self.current_bet == 0 else "raise"
            self.act(index, action, self._legal_bot_target(legal, desired))
        else:
            self.act(index, "check")

    def _advance_after_action(self, index: int, raised: bool) -> None:
        remaining = self.active_indices
        if len(remaining) == 1:
            winner = self.players[remaining[0]]
            amount = self.pot
            winner.stack += amount
            self._clear_contributions()
            self.hand_over = True
            self.actor_index = None
            self.last_result = f"{winner.name} wins {amount}; everyone else folded."
            self._event(self.last_result, "result")
            return

        if raised:
            self.pending = {
                seat
                for seat in self._eligible_to_act()
                if seat != index
            }
        else:
            self.pending.discard(index)
        self.pending.intersection_update(self._eligible_to_act())

        if not self.pending:
            self._finish_street()
            return
        self.actor_index = self._first_pending_from(index)

    def _finish_street(self) -> None:
        self._refund_unmatched_contribution()
        if len(self.board) == 5:
            self._showdown()
            return
        if len(self._eligible_to_act()) <= 1:
            self._runout_and_showdown()
            return

        for player in self.players:
            player.street_contribution = 0
        self.current_bet = 0
        self.minimum_raise = self.big_blind
        self.street_actions = []
        if len(self.board) == 0:
            self.stage = GameStage.FLOP
            self._deal_board(3)
        elif len(self.board) == 3:
            self.stage = GameStage.TURN
            self._deal_board(1)
        else:
            self.stage = GameStage.RIVER
            self._deal_board(1)
        self.pending = set(self._eligible_to_act())
        first = self._next_live(self.dealer_index)
        self.actor_index = self._first_pending_from(first, include_start=True)
        if self.actor_index is None:
            self._runout_and_showdown()

    def _runout_and_showdown(self) -> None:
        while len(self.board) < 5:
            if len(self.board) == 0:
                self.stage = GameStage.FLOP
                self._deal_board(3)
            elif len(self.board) == 3:
                self.stage = GameStage.TURN
                self._deal_board(1)
            else:
                self.stage = GameStage.RIVER
                self._deal_board(1)
        self._showdown()

    def _showdown(self) -> None:
        self._refund_unmatched_contribution()
        scores = {
            index: self._score(player)
            for index, player in enumerate(self.players)
            if player.in_hand
        }
        for index in scores:
            player = self.players[index]
            self._event(
                f"{player.name} shows {self._cards(player.hole_cards or ())} — "
                f"{self.evaluator.class_to_string(self.evaluator.get_rank_class(scores[index]))}."
            )

        levels = sorted(
            {player.total_contribution for player in self.players if player.total_contribution > 0}
        )
        previous = 0
        awards: dict[int, int] = {}
        for level in levels:
            contributors = [
                index
                for index, player in enumerate(self.players)
                if player.total_contribution >= level
            ]
            side_pot = (level - previous) * len(contributors)
            eligible = [index for index in contributors if index in scores]
            best = min(scores[index] for index in eligible)
            winners = [index for index in eligible if scores[index] == best]
            share, odd = divmod(side_pot, len(winners))
            for winner in winners:
                awards[winner] = awards.get(winner, 0) + share
            for winner in self._clockwise_winners(winners)[:odd]:
                awards[winner] = awards.get(winner, 0) + 1
            previous = level

        for index, amount in awards.items():
            self.players[index].stack += amount
            self._event(f"{self.players[index].name} wins {amount}.", "result")
        self._clear_contributions()
        self.hand_over = True
        self.actor_index = None
        self.last_result = "; ".join(
            f"{self.players[index].name} +{amount}" for index, amount in awards.items()
        )

    def _deal_board(self, count: int) -> None:
        self.deck.pop()
        self.board.extend(self.deck.pop() for _ in range(count))
        self._event(f"{self.stage.value.title()}: {self._cards(self.board)}", "street")

    def _post_blind(self, index: int, amount: int, name: str) -> None:
        paid = self._pay(index, min(amount, self.players[index].stack))
        self._event(f"{self.players[index].name} posts {name}: {paid}.")

    def _pay(self, index: int, amount: int) -> int:
        player = self.players[index]
        paid = min(max(amount, 0), player.stack)
        player.stack -= paid
        player.street_contribution += paid
        player.total_contribution += paid
        return paid

    def _refund_unmatched_contribution(self) -> None:
        contributions = sorted(
            (player.street_contribution for player in self.players if player.in_hand),
            reverse=True,
        )
        if len(contributions) < 2 or contributions[0] == contributions[1]:
            return
        largest = max(
            (player for player in self.players if player.in_hand),
            key=lambda player: player.street_contribution,
        )
        refund = contributions[0] - contributions[1]
        largest.street_contribution -= refund
        largest.total_contribution -= refund
        largest.stack += refund
        self._event(f"{largest.name} receives {refund} unmatched chips back.")

    def _clear_contributions(self) -> None:
        for player in self.players:
            player.street_contribution = 0
            player.total_contribution = 0

    def _score(self, player: TablePlayer) -> int:
        assert player.hole_cards is not None
        board = [TreysCard.new(str(card)) for card in self.board]
        hand = [TreysCard.new(str(card)) for card in player.hole_cards]
        return self.evaluator.evaluate(board, hand)

    def _eligible_to_act(self) -> list[int]:
        return [
            index
            for index, player in enumerate(self.players)
            if player.in_hand and player.stack > 0
        ]

    def _next_funded(self, index: int) -> int:
        for offset in range(1, len(self.players) + 1):
            candidate = (index + offset) % len(self.players)
            if self.players[candidate].stack > 0:
                return candidate
        raise RuntimeError("No funded player")

    def _next_live(self, index: int) -> int:
        for offset in range(1, len(self.players) + 1):
            candidate = (index + offset) % len(self.players)
            player = self.players[candidate]
            if player.hole_cards is not None and not player.folded:
                return candidate
        raise RuntimeError("No live player")

    def _first_pending_from(self, index: int, include_start: bool = False) -> int | None:
        start = 0 if include_start else 1
        for offset in range(start, len(self.players) + start):
            candidate = (index + offset) % len(self.players)
            if candidate in self.pending:
                return candidate
        return None

    def _position_for(self, index: int) -> Position:
        live = self.active_indices
        if len(live) <= 2 or index == self.dealer_index:
            return Position.LATE
        distance = (index - self.dealer_index) % len(self.players)
        return Position.EARLY if distance <= max(1, len(live) // 3) else Position.MIDDLE

    def _legal_bot_target(self, legal: dict[str, int | bool], desired: int) -> int:
        minimum = int(legal["minimum_total"])
        maximum = int(legal["maximum_total"])
        return min(max(desired, minimum), maximum)

    def _clockwise_winners(self, winners: list[int]) -> list[int]:
        return sorted(
            winners,
            key=lambda index: (index - self.dealer_index - 1) % len(self.players),
        )

    def _seed_for(self, salt: int) -> int | None:
        return None if self.seed is None else self.seed + salt + 1

    def _event(self, text: str, kind: str = "info") -> None:
        self.events.append(TableEvent(text, kind))

    @staticmethod
    def _cards(cards: tuple[Card, ...] | list[Card]) -> str:
        return " ".join(str(card) for card in cards)
