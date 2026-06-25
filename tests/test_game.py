import pytest

from poker_ai.cards import Card
from poker_ai.evaluator import EquityResult
from poker_ai.game import (
    HeadsUpGame,
    PlayerCommand,
    TerminalPokerGame,
    parse_player_command,
    prompt_for_bot_count,
)
from poker_ai.opponents import ObservedAction
from poker_ai.strategy import Action, Decision, GameStage


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("f", PlayerCommand("fold")),
        ("check", PlayerCommand("check")),
        ("call", PlayerCommand("call")),
        ("bet 25", PlayerCommand("bet", 25)),
        ("raise 60", PlayerCommand("raise", 60)),
        ("all-in", PlayerCommand("allin")),
    ],
)
def test_parse_player_commands(text: str, expected: PlayerCommand) -> None:
    assert parse_player_command(text) == expected


def test_human_call_moves_chips_to_pot() -> None:
    game = HeadsUpGame(starting_stack=100, simulations=10, seed=1)
    game.players["you"].street_contribution = 5
    game.players["bot"].street_contribution = 10
    game.pot = 15

    action, paid = game._apply_human_command(
        PlayerCommand("call"), to_call=5, current_bet=10, last_raise=10
    )

    assert action == ObservedAction.CALL
    assert paid == 5
    assert game.players["you"].stack == 95
    assert game.pot == 20


def test_raise_amount_is_total_for_the_street() -> None:
    game = HeadsUpGame(starting_stack=100, simulations=10, seed=1)
    game.players["you"].street_contribution = 5
    game.players["bot"].street_contribution = 10
    game.pot = 15

    action, paid = game._apply_human_command(
        PlayerCommand("raise", 30), to_call=5, current_bet=10, last_raise=10
    )

    assert action == ObservedAction.RAISE
    assert paid == 25
    assert game.players["you"].street_contribution == 30
    assert game.pot == 40


class BettingEngine:
    def decide(self, state):
        equity = EquityResult(0.8, 0.8, 0.0, 1)
        return Decision(Action.BET, 12, 0.8, 0.0, False, "test", equity)


def test_bot_big_blind_option_becomes_a_legal_raise() -> None:
    game = HeadsUpGame(starting_stack=100, simulations=10, seed=1)
    game.engine = BettingEngine()
    game.players["bot"].hole_cards = (
        Card.parse("As"),
        Card.parse("Kh"),
    )
    game.players["you"].street_contribution = 10
    game.players["bot"].street_contribution = 10
    game.players["you"].stack = 90
    game.players["bot"].stack = 90
    game.pot = 20

    action, paid = game._bot_turn(
        GameStage.PREFLOP, to_call=0, current_bet=10, last_raise=10
    )

    assert action == ObservedAction.RAISE
    assert game.players["bot"].street_contribution >= 20
    assert paid >= 10


def test_prompt_for_bot_count_retries_invalid_values() -> None:
    answers = iter(["zero", "0", "8", "3"])
    messages = []
    assert prompt_for_bot_count(lambda _: next(answers), messages.append) == 3
    assert len(messages) == 3


def test_terminal_game_creates_requested_bots() -> None:
    game = TerminalPokerGame(bot_count=4, simulations=10, seed=1)
    assert len(game.table.players) == 5
    assert game.table.players[0].name == "You"
    assert sum(player.is_bot for player in game.table.players) == 4
