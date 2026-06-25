import pytest

from poker_ai.table import MultiplayerTable


def passive_action(table: MultiplayerTable) -> None:
    index = table.actor_index
    assert index is not None
    legal = table.legal_actions(index)
    table.act(index, "call" if legal["to_call"] else "check")


def test_table_accepts_humans_and_bots_up_to_eight_seats() -> None:
    table = MultiplayerTable(3, 5, simulations=10, seed=1)
    assert len(table.players) == 8
    assert sum(not player.is_bot for player in table.players) == 3
    assert sum(player.is_bot for player in table.players) == 5


@pytest.mark.parametrize(("humans", "bots"), [(1, 0), (1, 8), (0, 2)])
def test_table_rejects_invalid_player_counts(humans: int, bots: int) -> None:
    with pytest.raises(ValueError):
        MultiplayerTable(humans, bots, simulations=10)


def test_passive_multiplayer_hand_reaches_showdown_and_preserves_chips() -> None:
    table = MultiplayerTable(3, 0, starting_stack=100, simulations=10, seed=4)
    table.start_hand()
    steps = 0
    while not table.hand_over:
        passive_action(table)
        steps += 1
        assert steps < 50

    assert len(table.board) == 5
    assert sum(player.stack for player in table.players) == 300
    assert table.pot == 0


def test_raise_reopens_action_for_other_players() -> None:
    table = MultiplayerTable(3, 0, starting_stack=100, simulations=10, seed=2)
    table.start_hand()
    raiser = table.actor_index
    assert raiser is not None
    legal = table.legal_actions(raiser)
    table.act(raiser, "raise", int(legal["minimum_total"]))

    assert raiser not in table.pending
    assert len(table.pending) == 2
    assert table.current_bet >= table.big_blind * 2

