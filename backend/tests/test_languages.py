from __future__ import annotations

import json
import unittest
from pathlib import Path

from hearally.languages import (
    SUPPORTED_LANGUAGE_TAGS,
    UnsupportedLanguageError,
    normalize_output_language,
    text_direction,
)


class LanguageTests(unittest.TestCase):
    def test_backend_catalog_matches_shared_web_catalog(self) -> None:
        catalog_path = Path(__file__).parents[2] / "shared" / "languages.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(tuple(language["tag"] for language in catalog), SUPPORTED_LANGUAGE_TAGS)

    def test_normalizes_region_to_a_supported_base_language(self) -> None:
        self.assertEqual(normalize_output_language("es-MX"), "es")
        self.assertEqual(normalize_output_language("pt_BR"), "pt")
        self.assertEqual(normalize_output_language("zh-hant"), "zh-Hant")

    def test_requires_an_explicit_chinese_script(self) -> None:
        with self.assertRaises(UnsupportedLanguageError):
            normalize_output_language("zh")

    def test_exposes_right_to_left_direction(self) -> None:
        self.assertEqual(text_direction("ar"), "rtl")
        self.assertEqual(text_direction("en"), "ltr")


if __name__ == "__main__":
    unittest.main()
