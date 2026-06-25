"""Hand evaluation, draw analysis, and Monte Carlo equity estimation."""

from __future__ import annotations

import random
from collections import Counter, OrderedDict
from dataclasses import dataclass
from functools import lru_cache
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
    """Estimate equity with lightweight action-weighted opponent ranges."""

    def __init__(
        self,
        simulations: int = 5_000,
        seed: int | None = None,
        cache_size: int = 256,
    ) -> None:
        if simulations < 1:
            raise ValueError("simulations must be at least 1")
        self.simulations = simulations
        self._rng = random.Random(seed)
        self._evaluator = Evaluator()
        self._treys_cards = {
            card: TreysCard.new(str(card)) for card in full_deck()
        }
        self._cache_size = max(cache_size, 0)
        self._cache: OrderedDict[tuple[object, ...], EquityResult] = OrderedDict()

    def estimate(
        self,
        hole_cards: Sequence[Card],
        community_cards: Sequence[Card],
        num_opponents: int,
        simulations: int | None = None,
        range_strengths: Sequence[float] | None = None,
    ) -> EquityResult:
        if len(hole_cards) != 2:
            raise ValueError("Texas Hold'em requires exactly two hole cards")
        if len(community_cards) > 5:
            raise ValueError("There can be at most five community cards")
        if num_opponents < 1:
            raise ValueError("num_opponents must be at least 1")
        trials = simulations or self.simulations
        if trials < 1:
            raise ValueError("simulations must be at least 1")
        strengths = tuple(
            max(0.0, min(float(value), 1.0))
            for value in (range_strengths or (0.0,) * num_opponents)
        )
        if len(strengths) != num_opponents:
            raise ValueError("range_strengths must match num_opponents")

        known = tuple(hole_cards) + tuple(community_cards)
        ensure_unique(known)
        cache_key = (
            tuple(sorted(str(card) for card in hole_cards)),
            tuple(str(card) for card in community_cards),
            num_opponents,
            trials,
            tuple(round(value, 2) for value in strengths),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            return cached

        known_set = set(known)
        # Preserve deck order so a fixed seed is repeatable across processes.
        unknown = [card for card in full_deck() if card not in known_set]
        cards_needed = (5 - len(community_cards)) + (2 * num_opponents)
        if cards_needed > len(unknown):
            raise ValueError("Not enough unknown cards for the requested opponents")

        hero = [self._treys_cards[card] for card in hole_cards]
        equity_total = 0.0
        outright_wins = 0
        ties = 0
        weighted_ranges = any(strengths)

        for _ in range(trials):
            if weighted_ranges:
                remaining = unknown.copy()
                opponent_hands = []
                for strength in strengths:
                    hand, positions = self._sample_weighted_hand(
                        remaining, strength
                    )
                    opponent_hands.append(hand)
                    for position in sorted(positions, reverse=True):
                        remaining.pop(position)
                runout = self._rng.sample(remaining, 5 - len(community_cards))
            else:
                dealt = self._rng.sample(unknown, cards_needed)
                opponent_hands = [
                    dealt[index : index + 2]
                    for index in range(0, 2 * num_opponents, 2)
                ]
                runout = dealt[2 * num_opponents :]
            board_cards = list(community_cards) + runout
            board = [self._treys_cards[card] for card in board_cards]

            hero_score = self._evaluator.evaluate(board, hero)
            opponent_scores = [
                self._evaluator.evaluate(
                    board, [self._treys_cards[card] for card in opponent]
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

        result = EquityResult(
            equity=equity_total / trials,
            outright_win_rate=outright_wins / trials,
            tie_rate=ties / trials,
            simulations=trials,
        )
        if self._cache_size:
            self._cache[cache_key] = result
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return result

    def _sample_weighted_hand(
        self, remaining: Sequence[Card], range_strength: float
    ) -> tuple[list[Card], tuple[int, int]]:
        if range_strength <= 0.02:
            positions = self._random_distinct_positions(len(remaining))
            return [remaining[position] for position in positions], positions
        # A small candidate pool captures most of the useful range bias without
        # the former ten rejection-sampling attempts per opponent.
        candidate_count = 2 + int(range_strength * 2.0)
        candidate_positions = [
            self._random_distinct_positions(len(remaining))
            for _ in range(candidate_count)
        ]
        candidates = [
            [remaining[first], remaining[second]]
            for first, second in candidate_positions
        ]
        if self._rng.random() < range_strength:
            selected = max(
                range(len(candidates)),
                key=lambda index: starting_hand_strength(candidates[index]),
            )
        else:
            selected = self._rng.randrange(len(candidates))
        return candidates[selected], candidate_positions[selected]

    def _random_distinct_positions(self, length: int) -> tuple[int, int]:
        first = self._rng.randrange(length)
        second = self._rng.randrange(length - 1)
        if second >= first:
            second += 1
        return first, second


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
    windows = [
        ranks.intersection(range(start, start + 5))
        for start in range(1, 11)
    ]
    best_window = max((len(window) for window in windows), default=0)
    if best_window == 4:
        four_card_windows = [
            window for window in windows if len(window) == 4
        ]
        open_ended = any(
            max(window) - min(window) == 3 and min(window) not in {1, 10}
            for window in four_card_windows
        )
        score += 0.44 if open_ended else 0.30
    elif best_window == 3:
        score += 0.14

    if community_cards:
        board_high = max(card.rank_value for card in community_cards)
        overcards = sum(card.rank_value > board_high for card in hole_cards)
        score += 0.08 * overcards
    elif abs(hole_cards[0].rank_value - hole_cards[1].rank_value) <= 2:
        score += 0.10

    return min(score, 1.0)


def starting_hand_strength(cards: Sequence[Card]) -> float:
    """Cheap 0..1 preflop quality score used by charts and range sampling."""

    if len(cards) != 2:
        raise ValueError("Starting-hand strength requires two cards")
    high, low = sorted((card.rank_value for card in cards), reverse=True)
    suited = cards[0].suit == cards[1].suit
    return _starting_class_strength(high, low, suited)


@lru_cache(maxsize=169)
def _starting_class_strength(high: int, low: int, suited: bool) -> float:
    """Precompute each of the 169 canonical Hold'em starting-hand classes."""

    pair = high == low
    gap = high - low
    if pair:
        return min(1.0, 0.48 + (high / 27.0))

    score = ((high - 2) / 12.0) * 0.52 + ((low - 2) / 12.0) * 0.22
    if suited:
        score += 0.08
    if gap == 1:
        score += 0.09
    elif gap == 2:
        score += 0.05
    elif gap == 3:
        score += 0.015
    elif gap >= 5:
        score -= 0.055
    if high == 14:
        score += 0.06
    if low < 6 and high < 11:
        score -= 0.04
    return max(0.0, min(score, 1.0))
