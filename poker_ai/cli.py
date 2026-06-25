"""Command-line demo for the poker decision engine."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .cards import parse_cards
from .evaluator import MonteCarloEvaluator
from .opponents import ObservedAction, OpponentAction, OpponentTracker
from .strategy import GameStage, GameState, Position, StrategyEngine


def _opponent_action(value: str) -> OpponentAction:
    try:
        player, action, amount = value.split(":", maxsplit=2)
        return OpponentAction(player, ObservedAction(action.lower()), float(amount))
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            "Actions must use player:action:amount, e.g. villain:raise:40"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate Hold'em equity and choose a poker action."
    )
    parser.add_argument("--hole", nargs=2, default=["As", "Kh"], metavar=("C1", "C2"))
    parser.add_argument("--board", nargs="*", default=["Qs", "Jh", "2c"])
    parser.add_argument("--pot", type=float, default=100.0)
    parser.add_argument("--call", type=float, default=20.0, dest="amount_to_call")
    parser.add_argument("--opponents", type=int, default=1)
    parser.add_argument(
        "--stage", choices=[stage.value for stage in GameStage], default="flop"
    )
    parser.add_argument("--can-check", action="store_true")
    parser.add_argument(
        "--position", choices=[position.value for position in Position], default="late"
    )
    parser.add_argument(
        "--action",
        action="append",
        type=_opponent_action,
        default=[],
        help="Observed action as player:action:amount; may be repeated.",
    )
    parser.add_argument("--simulations", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=42)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    hole = parse_cards(args.hole)
    board = parse_cards(args.board)
    tracker = OpponentTracker()
    tracker.record_many(args.action)
    evaluator = MonteCarloEvaluator(simulations=args.simulations, seed=args.seed)
    engine = StrategyEngine(evaluator=evaluator, tracker=tracker, seed=args.seed)
    state = GameState(
        hole_cards=(hole[0], hole[1]),
        community_cards=board,
        pot_size=args.pot,
        amount_to_call=args.amount_to_call,
        opponent_actions=tuple(args.action),
        num_opponents=args.opponents,
        stage=GameStage(args.stage),
        can_check=args.can_check,
        position=Position(args.position),
    )
    decision = engine.decide(state)
    output = asdict(decision)
    output["action"] = decision.action.value
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()

