from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
from date_extract import detect_first_date, select_content_date  # noqa: E402


class DateExtractTests(unittest.TestCase):
    def test_supported_full_date_formats(self) -> None:
        examples = {
            "2026-08-16": "2026-08-16",
            "Aug 16 2026": "2026-08-16",
            "August 16, 2026": "2026-08-16",
            "Aug 16th, 2026": "2026-08-16",
            "16 Aug 2026": "2026-08-16",
            "16th August 2026": "2026-08-16",
            "8/16/26": "2026-08-16",
            "16/8/26": "2026-08-16",
        }

        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(detect_first_date(text), expected)

    def test_month_year_uses_first_day(self) -> None:
        self.assertEqual(detect_first_date("August 2026"), "2026-08-01")

    def test_ambiguous_numeric_date_is_rejected(self) -> None:
        self.assertIsNone(detect_first_date("8/9/26"))

    def test_invalid_calendar_date_is_rejected(self) -> None:
        self.assertIsNone(detect_first_date("February 30 2026"))

    def test_words_starting_with_month_abbreviations_are_not_dates(self) -> None:
        self.assertIsNone(detect_first_date("Marching 2026"))

    def test_two_digit_year_pivot(self) -> None:
        self.assertEqual(detect_first_date("8/16/68"), "2068-08-16")
        self.assertEqual(detect_first_date("8/16/69"), "1969-08-16")

    def test_earliest_valid_date_wins_across_formats(self) -> None:
        text = "August 15 2026 then https://example.com/2026-07-30-story"
        self.assertEqual(detect_first_date(text), "2026-08-15")

    def test_source_precedence(self) -> None:
        self.assertEqual(
            select_content_date(
                context="Imported for 16 August 2026",
                title="Report August 15 2026",
                content="July 30 2026",
                metadata="2026-07-01",
            ),
            "2026-08-16",
        )

    def test_metadata_is_last_fallback(self) -> None:
        self.assertEqual(
            select_content_date(metadata="2026-07-01"),
            "2026-07-01",
        )


if __name__ == "__main__":
    unittest.main()
