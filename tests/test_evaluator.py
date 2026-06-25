from poker_ai.cards import parse_cards
from poker_ai.evaluator import (
    MonteCarloEvaluator,
    RangeProfile,
    board_affinity,
    draw_strength,
    starting_hand_strength,
)


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


def test_starting_hand_chart_ranks_premium_above_trash() -> None:
    assert starting_hand_strength(parse_cards(["As", "Ah"])) > starting_hand_strength(
        parse_cards(["7c", "2d"])
    )


def test_equity_cache_returns_same_result_without_more_sampling() -> None:
    evaluator = MonteCarloEvaluator(simulations=200, seed=8)
    first = evaluator.estimate(
        parse_cards(["As", "Kd"]),
        parse_cards(["Qs", "7h", "2c"]),
        1,
        simulations=120,
        range_strengths=(0.4,),
    )
    second = evaluator.estimate(
        parse_cards(["Kd", "As"]),
        parse_cards(["Qs", "7h", "2c"]),
        1,
        simulations=120,
        range_strengths=(0.4,),
    )
    assert first is second
    assert first.simulations == 120


def test_board_affinity_prefers_top_pair_to_unpaired_overcards() -> None:
    board = parse_cards(["Ks", "7h", "2c"])
    assert board_affinity(parse_cards(["Kd", "Qc"]), board) > board_affinity(
        parse_cards(["As", "Qd"]), board
    )


def test_range_profiles_are_supported_by_weighted_simulation() -> None:
    result = MonteCarloEvaluator(simulations=100, seed=3).estimate(
        parse_cards(["As", "Kd"]),
        parse_cards(["Qs", "7h", "2c"]),
        1,
        range_profiles=(RangeProfile(0.5, 0.6),),
    )
    assert 0.0 <= result.equity <= 1.0
