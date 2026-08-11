from __future__ import annotations

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

    def test_query_and_lint_titles_keep_existing_behavior(self) -> None:
        self._write(
            "2026-08-11_query-original-query.md",
            "# A heading that should not replace the query slug\n",
        )
        self._write("2026-08-11_lint.md", "# Custom lint heading\n")

        items = {item["kind"]: item for item in bridge._outputs_list()}

        self.assertEqual(items["query"]["title"], "Original query")
        self.assertEqual(items["lint"]["title"], "Lint report")


if __name__ == "__main__":
    unittest.main()
