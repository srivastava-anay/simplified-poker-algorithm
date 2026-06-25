from poker_ai.cards import parse_cards
from poker_ai.evaluator import MonteCarloEvaluator, draw_strength


def test_royal_flush_has_full_equity_on_complete_board() -> None:
    evaluator = MonteCarloEvaluator(simulations=100, seed=1)
    result = evaluator.estimate(
        parse_cards(["As", "Ks"]),
        parse_cards(["Qs", "Js", "Ts", "2d", "3c"]),
        num_opponents=2,
    )
    assert result.equity == 1.0


def test_flush_draw_has_backup_equity() -> None:
    strength = draw_strength(
        parse_cards(["As", "7s"]),
        parse_cards(["Ks", "2s", "Qd"]),
    )
    assert strength >= 0.55

