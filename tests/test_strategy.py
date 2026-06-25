from poker_ai.cards import parse_cards
from poker_ai.evaluator import EquityResult
from poker_ai.opponents import OpponentAction
from poker_ai.strategy import (
    Action,
    BluffSignals,
    BoardTexture,
    GameStage,
    GameState,
    Personality,
    Position,
    StrategyEngine,
)


class FixedEvaluator:
    def __init__(self, equity: float) -> None:
        self.equity = equity

    def estimate(self, hole_cards, community_cards, num_opponents) -> EquityResult:
        return EquityResult(self.equity, self.equity, 0.0, 1)


def state(*, call: float, can_check: bool) -> GameState:
    hole = parse_cards(["8c", "7d"])
    board = parse_cards(["As", "Kh", "2c"])
    return GameState(
        hole_cards=(hole[0], hole[1]),
        community_cards=board,
        pot_size=100,
        amount_to_call=call,
        opponent_actions=(),
        num_opponents=2,
        stage=GameStage.FLOP,
        can_check=can_check,
    )


def test_folds_when_equity_is_well_below_pot_odds() -> None:
    engine = StrategyEngine(evaluator=FixedEvaluator(0.10), seed=5)
    decision = engine.decide(state(call=50, can_check=False))
    assert decision.action == Action.FOLD


def test_checks_medium_or_weak_hand_when_free() -> None:
    engine = StrategyEngine(evaluator=FixedEvaluator(0.35), seed=5)
    decision = engine.decide(state(call=0, can_check=True))
    assert decision.action == Action.CHECK


def test_value_bets_a_strong_hand() -> None:
    engine = StrategyEngine(evaluator=FixedEvaluator(0.80), seed=5)
    decision = engine.decide(state(call=0, can_check=True))
    assert decision.action == Action.BET
    assert decision.amount > 0


def test_preflop_chart_opens_pocket_aces() -> None:
    hole = parse_cards(["As", "Ah"])
    engine = StrategyEngine(evaluator=FixedEvaluator(0.85), seed=2)
    decision = engine.decide(
        GameState(
            hole_cards=(hole[0], hole[1]),
            community_cards=(),
            pot_size=15,
            amount_to_call=0,
            opponent_actions=(),
            num_opponents=2,
            stage=GameStage.PREFLOP,
            can_check=True,
            position=Position.EARLY,
            big_blind=10,
        )
    )
    assert decision.action == Action.BET


def test_preflop_chart_rejects_trash_from_early_position() -> None:
    hole = parse_cards(["7c", "2d"])
    engine = StrategyEngine(evaluator=FixedEvaluator(0.20), seed=2)
    decision = engine.decide(
        GameState(
            hole_cards=(hole[0], hole[1]),
            community_cards=(),
            pot_size=15,
            amount_to_call=10,
            opponent_actions=(),
            num_opponents=4,
            stage=GameStage.PREFLOP,
            can_check=False,
            position=Position.EARLY,
            big_blind=10,
        )
    )
    assert decision.action == Action.FOLD


def test_personalities_have_distinct_settings() -> None:
    tight = StrategyEngine(
        evaluator=FixedEvaluator(0.5),
        personality=Personality.TIGHT_AGGRESSIVE,
    )
    loose = StrategyEngine(
        evaluator=FixedEvaluator(0.5),
        personality=Personality.LOOSE_AGGRESSIVE,
    )
    assert loose.settings.looseness > tight.settings.looseness


def test_dynamic_budget_uses_fewer_preflop_trials() -> None:
    from poker_ai.evaluator import MonteCarloEvaluator

    evaluator = MonteCarloEvaluator(simulations=1000, seed=1)
    engine = StrategyEngine(evaluator=evaluator, seed=1)
    hole = parse_cards(["As", "Kh"])
    preflop = GameState(
        hole_cards=(hole[0], hole[1]),
        community_cards=(),
        pot_size=15,
        amount_to_call=10,
        opponent_actions=(),
        num_opponents=1,
        stage=GameStage.PREFLOP,
        can_check=False,
        big_blind=10,
    )
    flop_cards = parse_cards(["Qs", "7h", "2c"])
    flop = GameState(
        hole_cards=(hole[0], hole[1]),
        community_cards=flop_cards,
        pot_size=30,
        amount_to_call=0,
        opponent_actions=(),
        num_opponents=1,
        stage=GameStage.FLOP,
        can_check=True,
        big_blind=10,
    )
    assert engine._simulation_budget(preflop) < engine._simulation_budget(flop)


def test_defends_slightly_below_raw_pot_odds() -> None:
    engine = StrategyEngine(evaluator=FixedEvaluator(0.22), seed=4)
    decision = engine.decide(state(call=30, can_check=False))
    assert decision.action == Action.CALL


def test_nut_flush_card_is_recognized_as_a_bluff_blocker() -> None:
    blocker = StrategyEngine._blocker_strength(
        parse_cards(["As", "Qh"]),
        parse_cards(["Ks", "9s", "5s", "2d", "3c"]),
    )
    assert blocker >= 0.5


def test_missed_turn_flush_draw_is_detected_on_river() -> None:
    missed = StrategyEngine._missed_draw_strength(
        parse_cards(["As", "7s"]),
        parse_cards(["Ks", "2s", "Qd", "4c", "9h"]),
    )
    assert missed >= 0.5


def test_loose_aggressive_personality_is_more_daring_on_average() -> None:
    hole = parse_cards(["8c", "7d"])
    board = parse_cards(["As", "Kh", "2c"])
    game_state = GameState(
        hole_cards=(hole[0], hole[1]),
        community_cards=board,
        pot_size=100,
        amount_to_call=0,
        opponent_actions=(),
        num_opponents=1,
        stage=GameStage.FLOP,
        can_check=True,
        position=Position.LATE,
    )
    tight = StrategyEngine(
        evaluator=FixedEvaluator(0.4),
        seed=9,
        personality=Personality.TIGHT_AGGRESSIVE,
    )
    loose = StrategyEngine(
        evaluator=FixedEvaluator(0.4),
        seed=9,
        personality=Personality.LOOSE_AGGRESSIVE,
    )
    tight_average = sum(tight._daring_factor(game_state) for _ in range(100)) / 100
    loose_average = sum(loose._daring_factor(game_state) for _ in range(100)) / 100
    assert loose_average > tight_average


def test_polarized_blocker_bluff_uses_a_large_size() -> None:
    hole = parse_cards(["As", "Qh"])
    board = parse_cards(["Ks", "9s", "5s", "2d", "3c"])
    game_state = GameState(
        hole_cards=(hole[0], hole[1]),
        community_cards=board,
        pot_size=100,
        amount_to_call=0,
        opponent_actions=(),
        num_opponents=1,
        stage=GameStage.RIVER,
        can_check=True,
        position=Position.LATE,
        effective_stack=500,
    )
    engine = StrategyEngine(evaluator=FixedEvaluator(0.2), seed=1)
    amount = engine._bluff_size(
        game_state,
        BoardTexture(0.5, 1, False, False),
        BluffSignals(0.0, 0.7, 0.0, 0.2, 0.5),
        daring=0.8,
    )
    assert amount >= 90


def test_blocker_bluffs_exist_but_are_not_automatic() -> None:
    hole = parse_cards(["As", "Qh"])
    board = parse_cards(["Ks", "9s", "5s", "2d", "3c"])
    game_state = GameState(
        hole_cards=(hole[0], hole[1]),
        community_cards=board,
        pot_size=100,
        amount_to_call=0,
        opponent_actions=(OpponentAction("v", "check", street="river"),),
        num_opponents=1,
        stage=GameStage.RIVER,
        can_check=True,
        position=Position.LATE,
        active_opponent_ids=("v",),
        effective_stack=500,
        big_blind=10,
    )
    results = [
        StrategyEngine(
            evaluator=FixedEvaluator(0.18),
            seed=seed,
            personality=Personality.LOOSE_AGGRESSIVE,
        ).decide(game_state)
        for seed in range(40)
    ]
    bluff_count = sum(decision.is_bluff for decision in results)
    assert 0 < bluff_count < len(results)


def test_delayed_continuation_bet_line_increases_bluff_support() -> None:
    hole = parse_cards(["Ac", "Qd"])
    board = parse_cards(["9s", "6h", "2c", "Kd"])
    engine = StrategyEngine(evaluator=FixedEvaluator(0.25), seed=1)
    common = dict(
        hole_cards=(hole[0], hole[1]),
        community_cards=board,
        pot_size=80,
        amount_to_call=0,
        opponent_actions=(OpponentAction("v", "check", street="turn"),),
        num_opponents=1,
        stage=GameStage.TURN,
        can_check=True,
        position=Position.LATE,
    )
    without_history = GameState(**common)
    delayed_line = GameState(
        **common,
        hero_actions=(
            OpponentAction("hero", "raise", street="preflop"),
            OpponentAction("hero", "check", street="flop"),
        ),
    )
    texture = engine._board_texture(board)
    baseline = engine._bluff_signals(without_history, 0.0, 0.0, texture)
    delayed = engine._bluff_signals(delayed_line, 0.0, 0.0, texture)
    assert delayed.opponent_weakness > baseline.opponent_weakness
