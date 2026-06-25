import pytest

from poker_ai.cards import Card, parse_cards


def test_card_normalizes_notation() -> None:
    assert str(Card.parse("aS")) == "As"


def test_duplicate_cards_are_rejected() -> None:
    with pytest.raises(ValueError, match="same card"):
        parse_cards(["As", "As"])

