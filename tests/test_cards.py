import pytest

from poker_ai.cards import Card, format_cards, parse_cards


def test_card_normalizes_notation() -> None:
    assert str(Card.parse("aS")) == "As"


def test_duplicate_cards_are_rejected() -> None:
    with pytest.raises(ValueError, match="same card"):
        parse_cards(["As", "As"])


def test_player_facing_cards_use_suit_symbols() -> None:
    assert format_cards(list(parse_cards(["As", "Th", "7d", "2c"]))) == "A♠ T♥ 7♦ 2♣"
