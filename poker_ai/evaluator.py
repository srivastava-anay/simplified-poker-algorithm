"""Hand evaluation, draw analysis, and Monte Carlo equity estimation."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from treys import Card as TreysCard
from treys import Evaluator

from .cards import Card, ensure_unique, full_deck


@dataclass(frozen=True)
class EquityResult:
    equity: float
    outright_win_rate: float
    tie_rate: float
    simulations: int


class MonteCarloEvaluator:
    """Estimate showdown equity against uniformly random unknown hands."""

    def __init__(self, simulations: int = 5_000, seed: int | None = None) -> None:
        if simulations < 1:
            raise ValueError("simulations must be at least 1")
        self.simulations = simulations
        self._rng = random.Random(seed)
        self._evaluator = Evaluator()

    def estimate(
        self,
        hole_cards: Sequence[Card],
        community_cards: Sequence[Card],
        num_opponents: int,
    ) -> EquityResult:
        if len(hole_cards) != 2:
            raise ValueError("Texas Hold'em requires exactly two hole cards")
        if len(community_cards) > 5:
            raise ValueError("There can be at most five community cards")
        if num_opponents < 1:
            raise ValueError("num_opponents must be at least 1")

        known = tuple(hole_cards) + tuple(community_cards)
        ensure_unique(known)
        known_set = set(known)
        # Preserve deck order so a fixed seed is repeatable across processes.
        unknown = [card for card in full_deck() if card not in known_set]
        cards_needed = (5 - len(community_cards)) + (2 * num_opponents)
        if cards_needed > len(unknown):
            raise ValueError("Not enough unknown cards for the requested opponents")

        hero = [self._to_treys(card) for card in hole_cards]
        equity_total = 0.0
        outright_wins = 0
        ties = 0

        for _ in range(self.simulations):
            dealt = self._rng.sample(unknown, cards_needed)
            opponent_hands = [
                dealt[index : index + 2]
                for index in range(0, 2 * num_opponents, 2)
            ]
            runout = dealt[2 * num_opponents :]
            board_cards = list(community_cards) + runout
            board = [self._to_treys(card) for card in board_cards]

            hero_score = self._evaluator.evaluate(board, hero)
            opponent_scores = [
                self._evaluator.evaluate(
                    board, [self._to_treys(card) for card in opponent]
                )
                for opponent in opponent_hands
            ]
            best_score = min([hero_score, *opponent_scores])
            if hero_score != best_score:
                continue

            winners = 1 + sum(score == best_score for score in opponent_scores)
            equity_total += 1.0 / winners
            if winners == 1:
                outright_wins += 1
            else:
                ties += 1

        return EquityResult(
            equity=equity_total / self.simulations,
            outright_win_rate=outright_wins / self.simulations,
            tie_rate=ties / self.simulations,
            simulations=self.simulations,
        )

    @staticmethod
    def _to_treys(card: Card) -> int:
        return TreysCard.new(str(card))


def draw_strength(hole_cards: Sequence[Card], community_cards: Sequence[Card]) -> float:
    """Return a lightweight 0..1 estimate of useful non-made-hand backup equity."""

    known = tuple(hole_cards) + tuple(community_cards)
    if len(community_cards) >= 5:
        return 0.0

    score = 0.0
    suit_counts = Counter(card.suit for card in known)
    hole_suits = {card.suit for card in hole_cards}
    if any(count == 4 and suit in hole_suits for suit, count in suit_counts.items()):
        score += 0.55
    elif len(community_cards) == 0 and hole_cards[0].suit == hole_cards[1].suit:
        score += 0.10

    ranks = {card.rank_value for card in known}
    if 14 in ranks:
        ranks.add(1)
    best_window = max(
        (len(ranks.intersection(range(start, start + 5))) for start in range(1, 11)),
        default=0,
    )
    if best_window == 4:
        score += 0.40
    elif best_window == 3:
        score += 0.14

    if community_cards:
        board_high = max(card.rank_value for card in community_cards)
        overcards = sum(card.rank_value > board_high for card in hole_cards)
        score += 0.08 * overcards
    elif abs(hole_cards[0].rank_value - hole_cards[1].rank_value) <= 2:
        score += 0.10

    return min(score, 1.0)
