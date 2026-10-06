"""Learning from "this was nothing": patterns, matching, and what is new."""

from __future__ import annotations

import unittest

from hearally.learning import (
    LEARNED_SECONDS,
    check,
    new_element_line,
    pattern_from,
    pattern_id,
)

NOW = 1_790_000_000.0
DANA = {"id": "a1", "name": "Dana"}


def heads_up(signals: list[str], grade: str = "priority") -> dict[str, object]:
    return {"id": "0" * 16, "category": "scam_pressure", "grade": grade, "signals": signals}


def learned(signals: list[str], category: str = "scam_pressure") -> dict[str, object]:
    pattern = pattern_from(
        {"id": "1" * 16, "category": category, "grade": "priority", "signals": signals},
        reason="known_caller",
        ally=DANA,
        now=NOW,
    )
    assert pattern is not None
    return pattern


class PatternTests(unittest.TestCase):
    def test_a_dismissed_heads_up_becomes_a_pattern_for_90_days(self) -> None:
        pattern = pattern_from(
            heads_up(["urgency", "asks_for_account_details"]),
            reason="known_caller",
            ally=DANA,
            now=NOW,
        )
        assert pattern is not None
        self.assertEqual(pattern["signals"], ["asks_for_account_details", "urgency"])
        self.assertEqual((pattern["reason"], pattern["ally_name"]), ("known_caller", "Dana"))
        self.assertEqual(pattern["ttl_epoch"], int(NOW) + LEARNED_SECONDS)

    def test_the_same_trigger_renews_the_same_pattern(self) -> None:
        self.assertEqual(
            pattern_id("scam_pressure", ["b", "a"]), pattern_id("scam_pressure", ["a", "b"])
        )
        self.assertNotEqual(pattern_id("scam_pressure", ["a"]), pattern_id("appointment", ["a"]))

    def test_a_daily_list_item_teaches_nothing(self) -> None:
        """The daily list is where a pattern sends things: there is nowhere lower."""
        self.assertIsNone(pattern_from(heads_up([], "daily"), reason="was_me", ally=DANA, now=NOW))


class CheckTests(unittest.TestCase):
    def test_nothing_new_matches(self) -> None:
        pattern = learned(["urgency", "asks_for_account_details"])
        for signals in (["urgency"], ["asks_for_account_details", "urgency"], []):
            with self.subTest(signals=signals):
                self.assertIs(check([pattern], "scam_pressure", signals, NOW).pattern, pattern)

    def test_a_new_element_does_not_match_and_is_named(self) -> None:
        result = check([learned(["urgency"])], "scam_pressure", ["urgency", "secrecy"], NOW)
        self.assertIsNone(result.pattern)
        self.assertEqual(result.new_signals, ("secrecy",))

    def test_another_kind_of_thing_is_unaffected(self) -> None:
        result = check([learned([])], "appointment", [], NOW)
        self.assertEqual((result.pattern, result.new_signals), (None, ()))

    def test_an_expired_pattern_is_ignored_even_before_ttl_deletes_it(self) -> None:
        pattern = learned(["urgency"])
        later = NOW + LEARNED_SECONDS + 1
        self.assertIsNone(check([pattern], "scam_pressure", ["urgency"], later).pattern)

    def test_the_new_element_line_reads_naturally(self) -> None:
        self.assertIn("also asking to keep it secret,", new_element_line(["secrecy"]))
        self.assertIn(
            "asking for payment and gift cards",
            new_element_line(["asks_for_payment", "gift_cards"]),
        )
        self.assertEqual(new_element_line([]), "")


if __name__ == "__main__":
    unittest.main()
