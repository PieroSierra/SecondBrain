from __future__ import annotations

import re as _re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import bridge  # noqa: E402
import ingest_state  # noqa: E402


class PromoteProposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        for sub in ("raw", "dashboard", "wiki", "outputs"):
            (self.vault / sub).mkdir()
        self.globals = mock.patch.multiple(
            bridge,
            VAULT_ROOT=self.vault,
            DASHBOARD_DIR=self.vault / "dashboard",
            RAW_DIR=self.vault / "raw",
            WIKI_DIR=self.vault / "wiki",
            OUTPUTS_DIR=self.vault / "outputs",
            INGEST_MANIFEST=self.vault / "raw" / ".ingest-manifest.json",
        )
        self.globals.start()
        self.handler = object.__new__(bridge.DashboardHandler)

    def tearDown(self) -> None:
        self.globals.stop()
        self.temp.cleanup()

    def test_build_promote_prompt(self) -> None:
        prompt, err = bridge._format_prompt(
            "promote", {"thread_file": "outputs/2026-09-06_thread-x.md"}
        )
        self.assertIsNone(err)
        self.assertEqual(
            prompt, '/second-brain-promote --thread "outputs/2026-09-06_thread-x.md"'
        )

    def test_promote_missing_thread_file_is_rejected(self) -> None:
        _prompt, err = bridge._format_prompt("promote", {})
        self.assertIsNotNone(err)

    def test_promote_is_read_only(self) -> None:
        (self.vault / "outputs" / "t.md").write_text("thread", encoding="utf-8")
        args = {"thread_file": "outputs/t.md"}

        def fake_run(prompt: str, cfg: dict, images=None) -> tuple[int, dict]:
            return 200, {
                "result": '```json\n{"promotions": []}\n```',
                "is_error": False,
            }

        with mock.patch.object(bridge, "run_skill", side_effect=fake_run):
            env = self.handler._run_kind(
                "promote",
                '/second-brain-promote --thread "outputs/t.md"',
                bridge.PROMPT_TEMPLATES["promote"],
                args,
            )
        self.assertEqual(env["__status__"], 200)
        self.assertIn('"promotions"', env["result"])
        # Read-only: nothing created anywhere.
        self.assertEqual(list((self.vault / "raw").rglob("*")), [])
        self.assertEqual(list((self.vault / "wiki").glob("*")), [])
        self.assertEqual(
            list((self.vault / "outputs").glob("*")),
            [self.vault / "outputs" / "t.md"],
        )


class PromoteApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        for sub in ("raw", "dashboard", "wiki", "outputs"):
            (self.vault / sub).mkdir()
        self.globals = mock.patch.multiple(
            bridge,
            VAULT_ROOT=self.vault,
            DASHBOARD_DIR=self.vault / "dashboard",
            RAW_DIR=self.vault / "raw",
            WIKI_DIR=self.vault / "wiki",
            OUTPUTS_DIR=self.vault / "outputs",
            INGEST_MANIFEST=self.vault / "raw" / ".ingest-manifest.json",
        )
        self.globals.start()
        ingest_state._HASH_CACHE.clear()
        self.handler = object.__new__(bridge.DashboardHandler)

    def tearDown(self) -> None:
        self.globals.stop()
        self.temp.cleanup()

    def _args(self):
        (self.vault / "outputs" / "2026-09-06_thread-personal-ai.md").write_text(
            "thread body", encoding="utf-8"
        )
        return {
            "thread_file": "outputs/2026-09-06_thread-personal-ai.md",
            "changes": [
                {
                    "type": "distinction",
                    "target_slug": "personal-ai",
                    "statement": "Durable synthesized knowledge differs from task context.",
                    "rationale": "Clarified while challenging the first answer.",
                }
            ],
        }

    def test_apply_writes_record_folds_article_and_finalizes(self) -> None:
        (self.vault / "wiki" / "personal-ai.md").write_text(
            "# Personal AI\n\nSummary.\n\n---\n*Sources: [[raw/about.md]] (2026-01-01)*\n",
            encoding="utf-8",
        )
        (self.vault / "wiki" / "INDEX.md").write_text(
            "# Knowledge Base Index\n", encoding="utf-8"
        )

        def fake_run(prompt: str, cfg: dict, images=None) -> tuple[int, dict]:
            scan_id = _re.search(r'--scan-id "([0-9a-f]+)"', prompt).group(1)
            record_rel = _re.search(r'--record "([^"]+)"', prompt).group(1)
            # The skill would read the record + fold it in. Simulate the write.
            art = self.vault / "wiki" / "personal-ai.md"
            body = art.read_text(encoding="utf-8")
            body = body.replace(
                "*Sources: [[raw/about.md]] (2026-01-01)*",
                f"*Sources: [[raw/about.md]] (2026-01-01), [[{record_rel}]] (2026-09-06)*",
            )
            art.write_text(
                body + "\nDurable synthesized knowledge differs.\n", encoding="utf-8"
            )
            (self.vault / "wiki" / "INDEX.md").write_text(
                "# Knowledge Base Index\nrebuilt\n", encoding="utf-8"
            )
            return 200, {
                "result": f'Done\n<!-- sb:promote-apply-complete scan_id="{scan_id}" -->',
                "is_error": False,
            }

        with mock.patch.object(bridge, "run_skill", side_effect=fake_run):
            env = self.handler._run_promote_apply(
                bridge.PROMPT_TEMPLATES["promote-apply"], self._args()
            )

        self.assertEqual(env["__status__"], 200)
        self.assertFalse(env.get("is_error"))
        # A record was written under raw/promotions/.
        records = list((self.vault / "raw" / "promotions").glob("*.md"))
        self.assertEqual(len(records), 1)
        rec_text = records[0].read_text(encoding="utf-8")
        self.assertIn(
            "Durable synthesized knowledge differs from task context.", rec_text
        )
        self.assertIn("kind: promotion", rec_text)
        # Touched article reported and its Sources footer cites the record.
        self.assertIn("personal-ai", env["articles"])
        art_text = (self.vault / "wiki" / "personal-ai.md").read_text(encoding="utf-8")
        self.assertIn("raw/promotions/", art_text)
        # Record is registered so a later ingest won't reprocess it.
        manifest, ok = ingest_state.load_manifest(self.vault)
        self.assertTrue(ok)
        self.assertIn(env["record"], manifest)

    def test_missing_marker_leaves_record_unregistered(self) -> None:
        with mock.patch.object(
            bridge,
            "run_skill",
            return_value=(200, {"result": "no marker", "is_error": False}),
        ):
            env = self.handler._run_promote_apply(
                bridge.PROMPT_TEMPLATES["promote-apply"], self._args()
            )
        self.assertTrue(env["is_error"])
        manifest, ok = ingest_state.load_manifest(self.vault)
        # Record file exists (self-heals via normal ingest) but is not finalized.
        self.assertFalse(ok and any(k.startswith("raw/promotions") for k in manifest))

    def test_empty_changes_rejected(self) -> None:
        env = self.handler._run_promote_apply(
            bridge.PROMPT_TEMPLATES["promote-apply"],
            {"thread_file": "outputs/2026-09-06_thread-personal-ai.md", "changes": []},
        )
        self.assertEqual(env["__status__"], 400)


if __name__ == "__main__":
    unittest.main()
