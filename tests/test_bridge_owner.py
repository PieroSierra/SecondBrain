from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import bridge  # noqa: E402


class SanitizeOwnerNameTests(unittest.TestCase):
    def test_keeps_a_typed_possessive_verbatim(self) -> None:
        self.assertEqual(bridge._sanitize_owner_name("Piero's"), "Piero's")

    def test_trims_surrounding_whitespace(self) -> None:
        self.assertEqual(bridge._sanitize_owner_name("  Piero's  "), "Piero's")

    def test_empty_input_resets_to_default(self) -> None:
        self.assertEqual(bridge._sanitize_owner_name("   "), bridge.OWNER_NAME_DEFAULT)

    def test_rejects_newline_injection(self) -> None:
        # A newline would inject a second .env line read at the next start.
        self.assertIsNone(bridge._sanitize_owner_name("Piero\nCLAUDE_BIN=/evil"))
        self.assertIsNone(bridge._sanitize_owner_name("Piero\rCLAUDE_BIN=/evil"))

    def test_strips_other_control_characters(self) -> None:
        self.assertEqual(bridge._sanitize_owner_name("Pie\x00ro's"), "Piero's")

    def test_rejects_over_length_rather_than_truncating(self) -> None:
        self.assertIsNone(bridge._sanitize_owner_name("x" * 33))

    def test_accepts_exactly_the_cap(self) -> None:
        self.assertEqual(bridge._sanitize_owner_name("x" * 32), "x" * 32)

    def test_rejects_non_string(self) -> None:
        self.assertIsNone(bridge._sanitize_owner_name(42))


class WriteEnvVarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        self.patch = mock.patch.object(bridge, "VAULT_ROOT", self.vault)
        self.patch.start()
        self.env = self.vault / ".env"

    def tearDown(self) -> None:
        self.patch.stop()
        self.temp.cleanup()

    def test_creates_env_when_absent(self) -> None:
        bridge._write_env_var("OWNER_NAME", "Piero's")

        self.assertEqual(self.env.read_text(encoding="utf-8"), "OWNER_NAME=Piero's\n")

    def test_preserves_other_keys_comments_and_order(self) -> None:
        self.env.write_text(
            "# personal config\n"
            "AGENT_ENGINE=claude\n"
            "OWNER_NAME=Old\n"
            "CRAFT_ENABLED=1\n",
            encoding="utf-8",
        )

        bridge._write_env_var("OWNER_NAME", "Piero's")

        self.assertEqual(
            self.env.read_text(encoding="utf-8"),
            "# personal config\n"
            "AGENT_ENGINE=claude\n"
            "OWNER_NAME=Piero's\n"
            "CRAFT_ENABLED=1\n",
        )

    def test_replaces_in_place_rather_than_appending(self) -> None:
        bridge._write_env_var("OWNER_NAME", "First")
        bridge._write_env_var("OWNER_NAME", "Second")

        body = self.env.read_text(encoding="utf-8")

        self.assertEqual(body.count("OWNER_NAME="), 1)
        self.assertIn("OWNER_NAME=Second", body)

    def test_appends_when_key_absent(self) -> None:
        self.env.write_text("AGENT_ENGINE=claude\n", encoding="utf-8")

        bridge._write_env_var("OWNER_NAME", "Piero's")

        self.assertEqual(
            self.env.read_text(encoding="utf-8"),
            "AGENT_ENGINE=claude\nOWNER_NAME=Piero's\n",
        )

    def test_ignores_a_commented_out_key(self) -> None:
        self.env.write_text("#OWNER_NAME=ignored\n", encoding="utf-8")

        bridge._write_env_var("OWNER_NAME", "Piero's")

        self.assertEqual(
            self.env.read_text(encoding="utf-8"),
            "#OWNER_NAME=ignored\nOWNER_NAME=Piero's\n",
        )

    def test_leaves_no_temp_files_behind(self) -> None:
        bridge._write_env_var("OWNER_NAME", "Piero's")

        self.assertEqual([p.name for p in self.vault.iterdir()], [".env"])


if __name__ == "__main__":
    unittest.main()
