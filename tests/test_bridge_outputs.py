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


if __name__ == "__main__":
    unittest.main()
