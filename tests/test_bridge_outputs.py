from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import bridge  # noqa: E402


class BridgeOutputsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.outputs = Path(self.temp.name) / "outputs"
        self.outputs.mkdir()
        self.output_patch = mock.patch.object(bridge, "OUTPUTS_DIR", self.outputs)
        self.output_patch.start()

    def tearDown(self) -> None:
        self.output_patch.stop()
        self.temp.cleanup()

    def _write(self, filename: str, content: str) -> None:
        (self.outputs / filename).write_text(content, encoding="utf-8")

    def test_thread_uses_stored_h1_as_title(self) -> None:
        self._write(
            "2026-08-11_thread-skyscanners-ai-software-operating-model.md",
            '<!-- sb:thread id="example" created="2026-08-11" -->\n\n'
            "# Skyscanner's AI Software Operating Model\n",
        )

        [item] = bridge._outputs_list()

        self.assertEqual(item["title"], "Skyscanner's AI Software Operating Model")

    def test_thread_without_h1_falls_back_to_filename_slug(self) -> None:
        self._write(
            "2026-08-11_thread-legacy-thread.md",
            '<!-- sb:thread id="legacy" created="2026-08-11" -->\n',
        )

        [item] = bridge._outputs_list()

        self.assertEqual(item["title"], "Legacy thread")

    def test_query_uses_stored_h1_and_lint_keeps_existing_behavior(self) -> None:
        self._write(
            "2026-08-11_query-original-query.md",
            "# A heading that should not replace the query slug\n",
        )
        self._write("2026-08-11_lint.md", "# Custom lint heading\n")

        items = {item["kind"]: item for item in bridge._outputs_list()}

        self.assertEqual(items["query"]["title"], "A heading that should not replace the query slug")
        self.assertEqual(items["lint"]["title"], "Lint report")

    def test_ingest_reports_are_listed_with_a_machine_title(self) -> None:
        # The listing regex is a closed set of kinds; an unlisted kind is
        # silently dropped from the sidebar, search and /outputs alike.
        self._write("2026-08-20_ingest.md", "# Ingest report\n")
        self._write("2026-08-20_ingest-2.md", "# Ingest report\n")

        items = bridge._outputs_list()

        self.assertEqual({i["kind"] for i in items}, {"ingest"})
        self.assertEqual({i["title"] for i in items}, {"Ingest report"})
        self.assertEqual(
            {i["filename"] for i in items},
            {"2026-08-20_ingest.md", "2026-08-20_ingest-2.md"},
        )

    def test_rename_replaces_only_first_h1(self) -> None:
        filename = "2026-08-11_thread-original.md"
        self._write(
            filename,
            '<!-- sb:thread id="example" -->\n\n# Original title\n\nBody\n\n# Later heading\n',
        )

        bridge._rename_output_title(filename, "My chosen title")

        self.assertEqual(
            (self.outputs / filename).read_text(encoding="utf-8"),
            '<!-- sb:thread id="example" -->\n\n# My chosen title\n\nBody\n\n# Later heading\n',
        )

    def test_rename_preserves_mtime_and_output_order(self) -> None:
        older = "2026-08-10_query-older.md"
        newer = "2026-08-11_query-newer.md"
        self._write(older, "# Older\n")
        self._write(newer, "# Newer\n")
        older_path = self.outputs / older
        newer_path = self.outputs / newer
        os.utime(older_path, ns=(1_000_000_000, 1_000_000_000))
        os.utime(newer_path, ns=(2_000_000_000, 2_000_000_000))

        bridge._rename_output_title(older, "Renamed older")

        self.assertEqual(older_path.stat().st_mtime_ns, 1_000_000_000)
        self.assertEqual(
            [item["filename"] for item in bridge._outputs_list()],
            [newer, older],
        )

    def test_rename_rejects_invalid_title_and_filename(self) -> None:
        filename = "2026-08-11_query-original.md"
        self._write(filename, "# Original\n")

        for title in ("", "two\nlines", "x" * 201):
            with self.subTest(title=title[:20]):
                with self.assertRaises(ValueError):
                    bridge._rename_output_title(filename, title)

        for unsafe in ("../query.md", "2026-08-11_lint.md", "notes.md"):
            with self.subTest(filename=unsafe):
                with self.assertRaises(ValueError):
                    bridge._rename_output_title(unsafe, "Title")

    def test_rename_requires_existing_file_with_h1(self) -> None:
        filename = "2026-08-11_query-no-heading.md"
        self._write(filename, "No heading here.\n")

        with self.assertRaises(LookupError):
            bridge._rename_output_title(filename, "New title")
        with self.assertRaises(FileNotFoundError):
            bridge._rename_output_title("2026-08-11_query-missing.md", "New title")


class ThreadEndSentinelTests(unittest.TestCase):
    """Deterministic end-of-thread anchor guaranteed before a follow-up appends."""

    SENTINEL = "<!-- sb:thread-end -->"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.outputs = Path(self.temp.name) / "outputs"
        self.outputs.mkdir()
        self.output_patch = mock.patch.object(bridge, "OUTPUTS_DIR", self.outputs)
        self.output_patch.start()

    def tearDown(self) -> None:
        self.output_patch.stop()
        self.temp.cleanup()

    def _write(self, filename: str, content: str) -> str:
        (self.outputs / filename).write_text(content, encoding="utf-8")
        return f"outputs/{filename}"

    def _read(self, filename: str) -> str:
        return (self.outputs / filename).read_text(encoding="utf-8")

    def test_appends_sentinel_when_missing(self) -> None:
        rel = self._write(
            "2026-08-22_thread-x.md",
            '<!-- sb:thread id="x" -->\n\n# X\n\n'
            '<!-- sb:turn role="user" ts="2026-08-22" -->\n## You\n\nHi\n',
        )
        bridge._ensure_thread_end_sentinel(rel)
        text = self._read("2026-08-22_thread-x.md")
        self.assertTrue(text.endswith(f"{self.SENTINEL}\n"))
        self.assertEqual(text.count(self.SENTINEL), 1)
        # Prior content is preserved.
        self.assertIn('<!-- sb:turn role="user" ts="2026-08-22" -->', text)
        self.assertIn("Hi", text)

    def test_idempotent_when_present_at_eof(self) -> None:
        rel = self._write(
            "2026-08-22_thread-y.md",
            f'<!-- sb:thread id="y" -->\n\n# Y\n\nBody\n\n{self.SENTINEL}\n',
        )
        before = self._read("2026-08-22_thread-y.md")
        bridge._ensure_thread_end_sentinel(rel)
        after = self._read("2026-08-22_thread-y.md")
        self.assertEqual(before, after)

    def test_collapses_multiple_sentinels_to_one(self) -> None:
        rel = self._write(
            "2026-08-22_thread-z.md",
            f'<!-- sb:thread id="z" -->\n\n# Z\n\n{self.SENTINEL}\n\nBody\n\n{self.SENTINEL}\n',
        )
        bridge._ensure_thread_end_sentinel(rel)
        text = self._read("2026-08-22_thread-z.md")
        self.assertEqual(text.count(self.SENTINEL), 1)
        self.assertTrue(text.endswith(f"{self.SENTINEL}\n"))
        # The real content survives the collapse.
        self.assertIn("Body", text)

    def test_single_trailing_newline(self) -> None:
        rel = self._write("2026-08-22_thread-nl.md", "Body\n\n\n\n")
        bridge._ensure_thread_end_sentinel(rel)
        text = self._read("2026-08-22_thread-nl.md")
        self.assertTrue(text.endswith(f"{self.SENTINEL}\n"))
        self.assertFalse(text.endswith(f"{self.SENTINEL}\n\n"))

    def test_preserves_turn_content(self) -> None:
        original = (
            '<!-- sb:thread id="p" -->\n\n# P\n\n'
            '<!-- sb:turn role="user" ts="2026-08-21" -->\n## You\n\nQ1\n\n'
            '<!-- sb:turn role="assistant" ts="2026-08-21" -->\n## Second Brain\n\nA1\n\n'
            "*Sources: [[wiki/a]]*\n"
        )
        rel = self._write("2026-08-22_thread-p.md", original)
        bridge._ensure_thread_end_sentinel(rel)
        text = self._read("2026-08-22_thread-p.md")
        # Everything before the appended anchor is byte-for-byte the original body.
        self.assertTrue(text.startswith(original.rstrip("\n")))

    def test_noop_on_missing_file(self) -> None:
        # Must not create the file.
        bridge._ensure_thread_end_sentinel("outputs/2026-08-22_thread-absent.md")
        self.assertFalse((self.outputs / "2026-08-22_thread-absent.md").exists())

    def test_noop_on_path_outside_outputs(self) -> None:
        # A traversal-y name must never escape outputs/; helper resolves by basename.
        bridge._ensure_thread_end_sentinel("outputs/../../etc/passwd")
        # Nothing created inside outputs, no exception raised.
        self.assertEqual(list(self.outputs.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
