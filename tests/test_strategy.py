from poker_ai.cards import parse_cards
from poker_ai.evaluator import EquityResult
from poker_ai.strategy import Action, GameStage, GameState, StrategyEngine


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

