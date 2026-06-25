from poker_ai.opponents import OpponentAction, OpponentTracker


def test_tracker_summarizes_aggression_and_weakness() -> None:
    tracker = OpponentTracker()
    tracker.record_many(
        [
            OpponentAction("villain", "check"),
            OpponentAction("villain", "fold"),
            OpponentAction("villain", "raise", 20),
        ]
    )
    profile = tracker.profile("villain")
    assert profile.weakness == 2 / 3
    assert profile.aggression > 0
    assert profile.aggressive_chips == 20


def test_tracker_learns_fold_to_bet_tendency() -> None:
    tracker = OpponentTracker()
    for _ in range(4):
        tracker.record(OpponentAction("villain", "fold", faced_bet=True))
    assert tracker.profile("villain").fold_to_bet > 0.6


def test_tracker_keeps_street_specific_tendencies() -> None:
    tracker = OpponentTracker()
    tracker.record(
        OpponentAction(
            "villain", "fold", faced_bet=True, street="river"
        )
    )
    tracker.record(OpponentAction("villain", "raise", street="flop"))
    profile = tracker.profile("villain")
    assert profile.street_fold_to_bet("river") > profile.street_fold_to_bet(
        "flop"
    )
    assert profile.street_aggression("flop") > 0
