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


@dataclass(frozen=True)
class RangeProfile:
    """Compact opponent range model used during weighted sampling."""

    preflop_strength: float = 0.0
    board_affinity: float = 0.0

    def clamped(self) -> "RangeProfile":
        return RangeProfile(
            max(0.0, min(self.preflop_strength, 1.0)),
            max(0.0, min(self.board_affinity, 1.0)),
        )


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
        self._cache_size = max(cache_size, 0)
        self._cache: OrderedDict[tuple[object, ...], EquityResult] = OrderedDict()

    def estimate(
        self,
        hole_cards: Sequence[Card],
        community_cards: Sequence[Card],
        num_opponents: int,
        simulations: int | None = None,
        range_strengths: Sequence[float] | None = None,
        range_profiles: Sequence[RangeProfile] | None = None,
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
        if range_profiles is not None:
            profiles = tuple(profile.clamped() for profile in range_profiles)
        else:
            profiles = tuple(
                RangeProfile(max(0.0, min(float(value), 1.0)), 0.0)
                for value in (range_strengths or (0.0,) * num_opponents)
            )
        if len(profiles) != num_opponents:
            raise ValueError("range profiles must match num_opponents")

        known = tuple(hole_cards) + tuple(community_cards)
        ensure_unique(known)
        cache_key = (
            tuple(sorted(str(card) for card in hole_cards)),
            tuple(str(card) for card in community_cards),
            num_opponents,
            trials,
            tuple(
                (
                    round(profile.preflop_strength, 2),
                    round(profile.board_affinity, 2),
                )
                for profile in profiles
            ),
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

        hero = [self._to_treys(card) for card in hole_cards]
        equity_total = 0.0
        outright_wins = 0
        ties = 0

        for _ in range(trials):
            if any(
                profile.preflop_strength > 0.0 or profile.board_affinity > 0.0
                for profile in profiles
            ):
                remaining = unknown.copy()
                opponent_hands = []
                for profile in profiles:
                    hand = self._sample_weighted_hand(
                        remaining, community_cards, profile
                    )
                    opponent_hands.append(hand)
                    remaining.remove(hand[0])
                    remaining.remove(hand[1])
                runout = self._rng.sample(remaining, 5 - len(community_cards))
            else:
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
        self,
        remaining: Sequence[Card],
        community_cards: Sequence[Card],
        profile: RangeProfile,
    ) -> list[Card]:
        if profile.preflop_strength <= 0.02 and profile.board_affinity <= 0.02:
            return self._rng.sample(remaining, 2)
        exponent = 1.0 + (3.5 * profile.preflop_strength)
        best_hand: list[Card] | None = None
        best_weight = -1.0
        candidate_count = 6 + int(4 * max(
            profile.preflop_strength, profile.board_affinity
        ))
        for _ in range(candidate_count):
            hand = self._rng.sample(remaining, 2)
            preflop_quality = starting_hand_strength(hand)
            board_quality = board_affinity(hand, community_cards)
            quality = (
                preflop_quality * (1.0 - 0.62 * profile.board_affinity)
                + board_quality * (0.62 * profile.board_affinity)
            )
            weight = (0.12 + quality) ** exponent
            if weight > best_weight:
                best_hand, best_weight = hand, weight
            if self._rng.random() <= weight:
                return hand
        assert best_hand is not None
        return best_hand

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


def board_affinity(
    hole_cards: Sequence[Card], community_cards: Sequence[Card]
) -> float:
    """Cheap made-hand/draw fit score for action-weighted range sampling."""

    if not community_cards:
        return starting_hand_strength(hole_cards)
    board_ranks = Counter(card.rank_value for card in community_cards)
    hole_ranks = Counter(card.rank_value for card in hole_cards)
    score = 0.0

    matches = sum(
        min(count, board_ranks.get(rank, 0))
        for rank, count in hole_ranks.items()
    )
    if matches:
        score += 0.34 + 0.18 * min(matches, 2)
    if hole_cards[0].rank_value == hole_cards[1].rank_value:
        score += 0.24
    if any(
        board_ranks.get(rank, 0) >= 2 for rank in hole_ranks
    ):
        score += 0.22

    score += 0.34 * draw_strength(hole_cards, community_cards)
    board_high = max(card.rank_value for card in community_cards)
    score += 0.055 * sum(
        card.rank_value > board_high for card in hole_cards
    )
    return max(0.0, min(score, 1.0))


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
