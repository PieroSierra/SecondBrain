from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import text_extract  # noqa: E402


class TextExtractDateTests(unittest.TestCase):
    def test_context_date_takes_precedence_over_title_and_content(self) -> None:
        result = text_extract.text_from_string(
            "July 30 2026\nUpdate body",
            title_hint="Update from Kirsty August 15 2026",
            context="Conversation created 16 August 2026",
        )

        self.assertEqual(result["content_date"], "2026-08-16")

    def test_title_date_takes_precedence_over_content_date(self) -> None:
        result = text_extract.text_from_string(
            "July 30 2026\nUpdate body",
            title_hint="Update from Kirsty August 15 2026",
        )

        self.assertEqual(result["content_date"], "2026-08-15")

    def test_first_content_date_wins_across_date_formats(self) -> None:
        result = text_extract.text_from_string(
            "August 15 2026\n"
            "Update body\n"
            "https://example.com/2026-07-30-story",
            title_hint="Kirsty update",
        )

        self.assertEqual(result["content_date"], "2026-08-15")

    def test_first_iso_content_date_still_wins_when_it_appears_first(self) -> None:
        result = text_extract.text_from_string(
            "2026-07-30\nFollow-up expected August 15 2026"
        )

        self.assertEqual(result["content_date"], "2026-07-30")


if __name__ == "__main__":
    unittest.main()
