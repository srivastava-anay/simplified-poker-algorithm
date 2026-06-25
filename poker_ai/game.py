"""Interactive heads-up Texas Hold'em game using the decision engine."""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from typing import Callable

from treys import Card as TreysCard
from treys import Evaluator

from .cards import Card, format_cards, full_deck
from .evaluator import MonteCarloEvaluator
from .opponents import ObservedAction, OpponentAction, OpponentTracker
from .strategy import Action, GameStage, GameState, Position, StrategyEngine
from .table import MultiplayerTable

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


class QuitGame(Exception):
    """Raised when the player leaves the interactive game."""


@dataclass
class Player:
    name: str
    stack: int
    hole_cards: tuple[Card, Card] | None = None
    street_contribution: int = 0
    folded: bool = False


@dataclass(frozen=True)
class PlayerCommand:
    action: str
    amount: int | None = None


def parse_player_command(text: str) -> PlayerCommand:
    """Parse commands such as ``call``, ``bet 20``, or ``raise 60``."""

    parts = text.strip().lower().replace("-", "").split()
    if not parts:
        raise ValueError("Enter an action.")
    aliases = {"f": "fold", "x": "check", "c": "call", "b": "bet", "r": "raise"}
    action = aliases.get(parts[0], parts[0])
    if action in {"quit", "exit", "q"}:
        return PlayerCommand("quit")
    if action in {"fold", "check", "call", "allin"}:
        if len(parts) != 1:
            raise ValueError(f"{action} does not take an amount.")
        return PlayerCommand(action)
    if action in {"bet", "raise"}:
        if len(parts) != 2:
            raise ValueError(f"Use '{action} AMOUNT'.")
        try:
            amount = int(parts[1])
        except ValueError as exc:
            raise ValueError("Chip amounts must be whole numbers.") from exc
        if amount <= 0:
            raise ValueError("Chip amounts must be positive.")
        return PlayerCommand(action, amount)
    raise ValueError("Use fold, check, call, bet N, raise N, all-in, or quit.")


class HeadsUpGame:
    """A compact no-limit heads-up match suitable for terminal play."""

    def __init__(
        self,
        starting_stack: int = 1_000,
        small_blind: int = 5,
        big_blind: int = 10,
        simulations: int = 3_000,
        seed: int | None = None,
        input_fn: InputFunction = input,
        output_fn: OutputFunction = print,
    ) -> None:
        if starting_stack <= 0:
            raise ValueError("starting_stack must be positive")
        if not 0 < small_blind <= big_blind:
            raise ValueError("Blinds must satisfy 0 < small blind <= big blind")
        self.players = {
            "you": Player("You", starting_stack),
            "bot": Player("Bot", starting_stack),
        }
        self.small_blind = small_blind
        self.big_blind = big_blind
        self._rng = random.Random(seed)
        self._input = input_fn
        self._output = output_fn
        self.tracker = OpponentTracker()
        self.engine = StrategyEngine(
            evaluator=MonteCarloEvaluator(simulations=simulations, seed=seed),
            tracker=self.tracker,
            seed=seed,
        )
        self.dealer = "you"
        self.hand_number = 0
        self.pot = 0
        self.board: list[Card] = []
        self.deck: list[Card] = []
        self.current_actions: list[OpponentAction] = []

    def run(self) -> None:
        self._output("\nHeads-up Texas Hold'em")
        self._output(
            "Commands: fold, check, call, bet N, raise N, all-in, quit"
        )
        self._output(
            f"Starting stacks: {self.players['you'].stack} each. "
            f"Blinds: {self.small_blind}/{self.big_blind}."
        )
        try:
            while all(player.stack > 0 for player in self.players.values()):
                self.play_hand()
                if all(player.stack > 0 for player in self.players.values()):
                    answer = self._input("\nPress Enter for the next hand, or q to quit: ")
                    if answer.strip().lower() in {"q", "quit", "exit"}:
                        raise QuitGame
        except (QuitGame, EOFError, KeyboardInterrupt):
            self._output("\nGame ended.")

        you = self.players["you"].stack
        bot = self.players["bot"].stack
        if you == 0:
            self._output("The bot wins the match.")
        elif bot == 0:
            self._output("You win the match!")
        self._output(f"Final stacks — You: {you}, Bot: {bot}")

    def play_hand(self) -> None:
        self.hand_number += 1
        self._start_hand()
        self._output(f"\n{'=' * 52}")
        self._output(
            f"Hand {self.hand_number} — button: {self.players[self.dealer].name}"
        )
        self._output(f"Your cards: {self._cards(self.players['you'].hole_cards or ())}")

        small_blind_player = self.dealer
        big_blind_player = self._other(self.dealer)
        self._post_blind(small_blind_player, self.small_blind, "small blind")
        self._post_blind(big_blind_player, self.big_blind, "big blind")

        winner = self._betting_round(GameStage.PREFLOP, self.dealer)
        if winner:
            self._award_fold(winner)
            self._finish_hand()
            return

        streets = (
            (GameStage.FLOP, 3),
            (GameStage.TURN, 1),
            (GameStage.RIVER, 1),
        )
        for stage, count in streets:
            if self._someone_all_in():
                self._deal_board(count, stage)
                continue
            self._deal_board(count, stage)
            self._reset_street()
            winner = self._betting_round(stage, self._other(self.dealer))
            if winner:
                self._award_fold(winner)
                self._finish_hand()
                return

        self._showdown()
        self._finish_hand()

    def _start_hand(self) -> None:
        self.pot = 0
        self.board = []
        self.current_actions = []
        self.deck = list(full_deck())
        self._rng.shuffle(self.deck)
        for player in self.players.values():
            player.hole_cards = (self.deck.pop(), self.deck.pop())
            player.street_contribution = 0
            player.folded = False

    def _finish_hand(self) -> None:
        self._output(
            f"Stacks — You: {self.players['you'].stack}, "
            f"Bot: {self.players['bot'].stack}"
        )
        self.tracker.mark_hand_seen("you")
        self.dealer = self._other(self.dealer)

    def _post_blind(self, player_id: str, amount: int, label: str) -> None:
        paid = self._pay(player_id, min(amount, self.players[player_id].stack))
        self._output(f"{self.players[player_id].name} posts {label}: {paid}")

    def _deal_board(self, count: int, stage: GameStage) -> None:
        self.deck.pop()  # burn card
        self.board.extend(self.deck.pop() for _ in range(count))
        self._output(f"\n{stage.value.title()}: {self._cards(self.board)}")

    def _reset_street(self) -> None:
        self.current_actions = []
        for player in self.players.values():
            player.street_contribution = 0

    def _betting_round(self, stage: GameStage, first_actor: str) -> str | None:
        actor_id = first_actor
        acted_since_raise: set[str] = set()
        current_bet = max(
            player.street_contribution for player in self.players.values()
        )
        last_raise = self.big_blind

        while True:
            actor = self.players[actor_id]
            opponent_id = self._other(actor_id)
            opponent = self.players[opponent_id]
            to_call = current_bet - actor.street_contribution

            if actor.stack == 0:
                if to_call > 0:
                    self._refund_unmatched()
                return None
            if opponent.stack == 0 and to_call == 0:
                return None

            self._show_state(stage, actor_id, to_call)
            old_current_bet = current_bet
            if actor_id == "you":
                action, paid = self._human_turn(to_call, current_bet, last_raise)
            else:
                action, paid = self._bot_turn(stage, to_call, current_bet, last_raise)

            if action == ObservedAction.FOLD:
                actor.folded = True
                if actor_id == "you":
                    self.tracker.record(OpponentAction("you", action, 0))
                return opponent_id

            if actor_id == "you":
                event = OpponentAction("you", action, paid)
                self.current_actions.append(event)
                self.tracker.record(event)

            current_bet = max(
                player.street_contribution for player in self.players.values()
            )
            if action in {ObservedAction.BET, ObservedAction.RAISE}:
                last_raise = max(current_bet - old_current_bet, last_raise)
                acted_since_raise = {actor_id}
            else:
                acted_since_raise.add(actor_id)

            contributions_match = (
                actor.street_contribution == opponent.street_contribution
            )
            if contributions_match and (
                len(acted_since_raise) == 2 or actor.stack == 0 or opponent.stack == 0
            ):
                return None
            actor_id = opponent_id

    def _human_turn(
        self, to_call: int, current_bet: int, last_raise: int
    ) -> tuple[ObservedAction, int]:
        while True:
            prompt = "Your action"
            prompt += f" ({to_call} to call)" if to_call else " (you may check)"
            try:
                command = parse_player_command(self._input(f"{prompt}: "))
                if command.action == "quit":
                    raise QuitGame
                return self._apply_human_command(
                    command, to_call, current_bet, last_raise
                )
            except ValueError as exc:
                self._output(f"Invalid action: {exc}")

    def _apply_human_command(
        self,
        command: PlayerCommand,
        to_call: int,
        current_bet: int,
        last_raise: int,
    ) -> tuple[ObservedAction, int]:
        player = self.players["you"]
        opponent = self.players["bot"]
        max_additional = min(player.stack, to_call + opponent.stack)
        max_total = player.street_contribution + max_additional

        if command.action == "fold":
            if to_call == 0:
                raise ValueError("You can check for free instead of folding.")
            self._output("You fold.")
            return ObservedAction.FOLD, 0
        if command.action == "check":
            if to_call:
                raise ValueError(f"You must call {to_call}, raise, or fold.")
            self._output("You check.")
            return ObservedAction.CHECK, 0
        if command.action == "call":
            if not to_call:
                raise ValueError("There is no bet to call; check instead.")
            paid = self._pay("you", min(to_call, player.stack))
            self._output(f"You call {paid}.")
            return ObservedAction.CALL, paid

        if command.action == "allin":
            target = max_total
        elif command.action == "bet":
            if current_bet:
                raise ValueError("A bet already exists; use raise N.")
            target = command.amount or 0
        else:
            if not current_bet:
                raise ValueError("No bet exists; use bet N.")
            target = command.amount or 0

        if target <= current_bet:
            raise ValueError(f"A raise must be above {current_bet}.")
        if target > max_total:
            raise ValueError(f"Maximum effective total is {max_total}.")
        minimum = self.big_blind if current_bet == 0 else current_bet + last_raise
        if target < minimum and target != max_total:
            raise ValueError(f"Minimum legal total is {minimum}.")

        paid = self._pay("you", target - player.street_contribution)
        action = ObservedAction.BET if current_bet == 0 else ObservedAction.RAISE
        verb = "bet" if action == ObservedAction.BET else "raise to"
        suffix = " (all-in)" if player.stack == 0 else ""
        self._output(f"You {verb} {target}{suffix}.")
        return action, paid

    def _bot_turn(
        self,
        stage: GameStage,
        to_call: int,
        current_bet: int,
        last_raise: int,
    ) -> tuple[ObservedAction, int]:
        bot = self.players["bot"]
        you = self.players["you"]
        assert bot.hole_cards is not None
        state = GameState(
            hole_cards=bot.hole_cards,
            community_cards=tuple(self.board),
            pot_size=self.pot,
            amount_to_call=to_call,
            opponent_actions=tuple(self.current_actions),
            num_opponents=1,
            stage=stage,
            can_check=to_call == 0,
            position=Position.LATE if self.dealer == "bot" else Position.EARLY,
        )
        decision = self.engine.decide(state)
        max_additional = min(bot.stack, to_call + you.stack)
        max_total = bot.street_contribution + max_additional

        if to_call:
            if decision.action == Action.FOLD:
                self._output(
                    f"Bot folds. [equity {decision.win_probability:.1%}]"
                )
                return ObservedAction.FOLD, 0
            if decision.action != Action.RAISE or max_total <= current_bet:
                paid = self._pay("bot", min(to_call, bot.stack))
                self._output(
                    f"Bot calls {paid}. [equity {decision.win_probability:.1%}]"
                )
                return ObservedAction.CALL, paid
            minimum = current_bet + last_raise
            desired_total = bot.street_contribution + max(
                int(round(decision.amount)), to_call
            )
            target = min(max(desired_total, minimum), max_total)
            paid = self._pay("bot", target - bot.street_contribution)
            suffix = " (all-in)" if bot.stack == 0 else ""
            self._output(
                f"Bot raises to {target}{suffix}. "
                f"[equity {decision.win_probability:.1%}]"
            )
            return ObservedAction.RAISE, paid

        if decision.action not in {Action.BET, Action.RAISE} or max_total == 0:
            self._output(
                f"Bot checks. [equity {decision.win_probability:.1%}]"
            )
            return ObservedAction.CHECK, 0

        desired = max(int(round(decision.amount)), self.big_blind)
        if current_bet:
            # This is the big blind's preflop option after the small blind calls.
            minimum = current_bet + last_raise
            target = min(
                max(bot.street_contribution + desired, minimum),
                max_total,
            )
            paid = self._pay("bot", target - bot.street_contribution)
            suffix = " (all-in)" if bot.stack == 0 else ""
            self._output(
                f"Bot raises to {target}{suffix}. "
                f"[equity {decision.win_probability:.1%}]"
            )
            return ObservedAction.RAISE, paid

        target = min(desired, max_total)
        paid = self._pay("bot", target - bot.street_contribution)
        suffix = " (all-in)" if bot.stack == 0 else ""
        self._output(
            f"Bot bets {target}{suffix}. [equity {decision.win_probability:.1%}]"
        )
        return ObservedAction.BET, paid

    def _show_state(self, stage: GameStage, actor_id: str, to_call: int) -> None:
        self._output(
            f"\n[{stage.value}] Pot: {self.pot} | "
            f"You: {self.players['you'].stack} | Bot: {self.players['bot'].stack}"
        )
        if actor_id == "you" and to_call:
            self._output(f"Amount to call: {to_call}")

    def _showdown(self) -> None:
        self._refund_unmatched()
        you = self.players["you"]
        bot = self.players["bot"]
        assert you.hole_cards is not None and bot.hole_cards is not None
        evaluator = Evaluator()
        board = [TreysCard.new(str(card)) for card in self.board]

        def score(player: Player) -> int:
            assert player.hole_cards is not None
            hand = [TreysCard.new(str(card)) for card in player.hole_cards]
            return evaluator.evaluate(board, hand)

        you_score = score(you)
        bot_score = score(bot)
        self._output("\nShowdown")
        self._output(f"You: {self._cards(you.hole_cards)}")
        self._output(f"Bot: {self._cards(bot.hole_cards)}")
        self._output(f"Board: {self._cards(self.board)}")
        if you_score < bot_score:
            self._award_pot("you", evaluator.class_to_string(evaluator.get_rank_class(you_score)))
        elif bot_score < you_score:
            self._award_pot("bot", evaluator.class_to_string(evaluator.get_rank_class(bot_score)))
        else:
            half = self.pot // 2
            self.players["you"].stack += half
            self.players["bot"].stack += half
            odd_chip = self.pot - (2 * half)
            self.players[self.dealer].stack += odd_chip
            self._output(f"Split pot: {half} each.")
            self.pot = 0

    def _award_fold(self, winner_id: str) -> None:
        self._refund_unmatched()
        self._award_pot(winner_id, "opponent folded")

    def _award_pot(self, winner_id: str, reason: str) -> None:
        amount = self.pot
        self.players[winner_id].stack += amount
        self.pot = 0
        self._output(f"{self.players[winner_id].name} wins {amount} ({reason}).")

    def _refund_unmatched(self) -> None:
        you = self.players["you"]
        bot = self.players["bot"]
        if you.street_contribution == bot.street_contribution:
            return
        larger = you if you.street_contribution > bot.street_contribution else bot
        smaller = bot if larger is you else you
        refund = larger.street_contribution - smaller.street_contribution
        larger.street_contribution -= refund
        larger.stack += refund
        self.pot -= refund
        self._output(f"{larger.name} receives {refund} unmatched chips back.")

    def _pay(self, player_id: str, amount: int) -> int:
        player = self.players[player_id]
        paid = min(max(amount, 0), player.stack)
        player.stack -= paid
        player.street_contribution += paid
        self.pot += paid
        return paid

    def _someone_all_in(self) -> bool:
        return any(player.stack == 0 for player in self.players.values())

    @staticmethod
    def _other(player_id: str) -> str:
        return "bot" if player_id == "you" else "you"

    @staticmethod
    def _cards(cards: tuple[Card, ...] | list[Card]) -> str:
        return format_cards(cards)


class TerminalPokerGame:
    """One human versus one or more bots using the multiplayer table engine."""

    def __init__(
        self,
        bot_count: int,
        starting_stack: int = 1_000,
        small_blind: int = 5,
        big_blind: int = 10,
        simulations: int = 2_000,
        seed: int | None = None,
        input_fn: InputFunction = input,
        output_fn: OutputFunction = print,
    ) -> None:
        if not 1 <= bot_count <= 7:
            raise ValueError("bot_count must be between 1 and 7")
        self.table = MultiplayerTable(
            human_players=1,
            bot_players=bot_count,
            starting_stack=starting_stack,
            small_blind=small_blind,
            big_blind=big_blind,
            simulations=simulations,
            seed=seed,
        )
        self.table.players[0].name = "You"
        self._input = input_fn
        self._output = output_fn
        self._event_cursor = 0

    def run(self) -> None:
        bots = len(self.table.players) - 1
        self._output(f"\nTexas Hold'em — You versus {bots} bot{'s' if bots != 1 else ''}")
        self._output("Commands: fold, check, call, bet N, raise N, all-in, quit")
        try:
            while not self.table.match_over and self.table.players[0].stack > 0:
                self.play_hand()
                if self.table.match_over or self.table.players[0].stack == 0:
                    break
                answer = self._input("\nPress Enter for the next hand, or q to quit: ")
                if answer.strip().lower() in {"q", "quit", "exit"}:
                    raise QuitGame
        except (QuitGame, EOFError, KeyboardInterrupt):
            self._output("\nGame ended.")

        self._show_stacks("Final stacks")
        if self.table.players[0].stack == 0:
            self._output("You are out of chips.")
        elif all(player.stack == 0 for player in self.table.players[1:]):
            self._output("You beat every bot!")

    def play_hand(self) -> None:
        self.table.start_hand()
        self._event_cursor = 0
        self._output(f"\n{'=' * 58}")
        human = self.table.players[0]
        self._output(f"Your cards: {self._cards(human.hole_cards or ())}")
        self._flush_events()

        while not self.table.hand_over:
            actor = self.table.actor
            if actor is None:
                break
            self._show_state()
            if actor.is_bot:
                self.table.bot_act()
            else:
                self._human_turn()
            self._flush_events()

        self._show_stacks()

    def _human_turn(self) -> None:
        index = self.table.actor_index
        assert index is not None
        legal = self.table.legal_actions(index)
        to_call = int(legal["to_call"])
        while True:
            prompt = f"Your action ({to_call} to call): " if to_call else "Your action (check is free): "
            try:
                command = parse_player_command(self._input(prompt))
                if command.action == "quit":
                    raise QuitGame
                self.table.act(index, command.action, command.amount)
                return
            except ValueError as exc:
                self._output(f"Invalid action: {exc}")

    def _show_state(self) -> None:
        actor = self.table.actor
        board = self._cards(self.table.board) or "(no community cards)"
        self._output(
            f"\n[{self.table.stage.value}] Board: {board} | "
            f"Pot: {self.table.pot} | Action: {actor.name if actor else 'none'}"
        )

    def _flush_events(self) -> None:
        for event in self.table.events[self._event_cursor :]:
            self._output(event.text)
        self._event_cursor = len(self.table.events)

    def _show_stacks(self, label: str = "Stacks") -> None:
        stacks = " | ".join(
            f"{player.name}: {player.stack}" for player in self.table.players
        )
        self._output(f"{label} — {stacks}")

    @staticmethod
    def _cards(cards: tuple[Card, ...] | list[Card]) -> str:
        return format_cards(cards)


def prompt_for_bot_count(
    input_fn: InputFunction = input,
    output_fn: OutputFunction = print,
) -> int:
    """Ask for 1–7 opponents until a valid number is entered."""

    while True:
        try:
            value = input_fn("How many bots do you want to play against? [1-7]: ")
            count = int(value)
            if 1 <= count <= 7:
                return count
        except ValueError:
            pass
        output_fn("Please enter a whole number from 1 to 7.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Play Texas Hold'em against one or more poker bots."
    )
    parser.add_argument(
        "--bots",
        type=int,
        choices=range(1, 8),
        default=None,
        metavar="N",
        help="number of bot opponents; prompted if omitted",
    )
    parser.add_argument("--stack", type=int, default=1_000, help="starting chips each")
    parser.add_argument("--small-blind", type=int, default=5)
    parser.add_argument("--big-blind", type=int, default=10)
    parser.add_argument("--simulations", type=int, default=3_000)
    parser.add_argument("--seed", type=int, default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    bot_count = args.bots if args.bots is not None else prompt_for_bot_count()
    game = TerminalPokerGame(
        bot_count=bot_count,
        starting_stack=args.stack,
        small_blind=args.small_blind,
        big_blind=args.big_blind,
        simulations=args.simulations,
        seed=args.seed,
    )
    game.run()
