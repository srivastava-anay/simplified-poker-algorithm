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
