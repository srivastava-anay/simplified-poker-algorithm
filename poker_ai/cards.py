"""Card representation and deck helpers."""

from __future__ import annotations

from dataclasses import dataclass

RANKS = "23456789TJQKA"
SUITS = "shdc"
RANK_VALUE = {rank: value for value, rank in enumerate(RANKS, start=2)}


@dataclass(frozen=True, order=True)
class Card:
    """A playing card using common poker notation, for example ``As``."""

    rank: str
    suit: str

    def __post_init__(self) -> None:
        rank = self.rank.upper()
        suit = self.suit.lower()
        if rank not in RANKS:
            raise ValueError(f"Invalid rank {self.rank!r}; expected one of {RANKS}")
        if suit not in SUITS:
            raise ValueError(f"Invalid suit {self.suit!r}; expected one of {SUITS}")
        object.__setattr__(self, "rank", rank)
        object.__setattr__(self, "suit", suit)

    @classmethod
    def parse(cls, text: str) -> "Card":
        value = text.strip()
        if len(value) != 2:
            raise ValueError(f"Invalid card {text!r}; use notation such as As, Td, or 7c")
        return cls(value[0], value[1])

    @property
    def rank_value(self) -> int:
        return RANK_VALUE[self.rank]

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"


def parse_cards(values: list[str] | tuple[str, ...]) -> tuple[Card, ...]:
    """Parse card strings and reject duplicates."""

    cards = tuple(Card.parse(value) for value in values)
    ensure_unique(cards)
    return cards


def ensure_unique(cards: tuple[Card, ...] | list[Card]) -> None:
    if len(set(cards)) != len(cards):
        raise ValueError("The same card cannot appear more than once")


def full_deck() -> tuple[Card, ...]:
    return tuple(Card(rank, suit) for rank in RANKS for suit in SUITS)

