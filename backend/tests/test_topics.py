"""Topics that never leave: the account holder's list, and finding mentions."""

from __future__ import annotations

import unittest

from hearally.models import ValidationError
from hearally.topics import MAX_TOPICS, mentioned, parse_topics


class ParseTests(unittest.TestCase):
    def test_trimmed_without_blanks_or_repeats(self) -> None:
        self.assertEqual(
            parse_topics(["  my  divorce ", "", "Lawyer", "lawyer", "Divorce"]),
            ["my divorce", "Lawyer", "Divorce"],
        )

    def test_limits(self) -> None:
        with self.assertRaises(ValidationError):
            parse_topics(["x" * 41])
        with self.assertRaises(ValidationError):
            parse_topics([f"t{n}" for n in range(MAX_TOPICS + 1)])
        with self.assertRaises(ValidationError):
            parse_topics("divorce")


class MentionedTests(unittest.TestCase):
    def test_whole_words_any_case_and_plurals(self) -> None:
        self.assertEqual(mentioned(["He saw two LAWYERS today."], ["lawyer"]), "lawyer")
        self.assertEqual(mentioned(["Talked about the\nhouse sale."], ["house sale"]), "house sale")

    def test_not_inside_other_words(self) -> None:
        self.assertIsNone(mentioned(["The class restarts at noon."], ["start"]))
        self.assertIsNone(mentioned(["Smart move."], ["art"]))

    def test_no_topics_no_mention(self) -> None:
        self.assertIsNone(mentioned(["anything"], []))


if __name__ == "__main__":
    unittest.main()
