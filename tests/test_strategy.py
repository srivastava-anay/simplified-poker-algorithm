from poker_ai.cards import parse_cards
from poker_ai.evaluator import EquityResult
from poker_ai.strategy import (
    Action,
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
