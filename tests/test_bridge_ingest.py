from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import bridge  # noqa: E402
import ingest_state  # noqa: E402


class BridgeIngestIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        (self.vault / "raw").mkdir()
        (self.vault / "dashboard").mkdir()
        (self.vault / "wiki").mkdir()
        (self.vault / "outputs").mkdir()
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
        bridge._raw_index_cache.clear()
        self.handler = object.__new__(bridge.DashboardHandler)

    def tearDown(self) -> None:
        self.globals.stop()
        self.temp.cleanup()

    def test_bridge_and_status_share_change_classification(self) -> None:
        source = self.vault / "raw" / "note.md"
        source.write_text("first", encoding="utf-8")
        entry = {
            "last_modified": "2026-01-01T00:00:00Z",
            "ingested_at": "2026-01-02T00:00:00Z",
            "fingerprint": ingest_state.fingerprint_file(source, use_cache=False),
        }
        (self.vault / "raw" / ".ingest-manifest.json").write_text(
            json.dumps({"raw/note.md": entry}), encoding="utf-8"
        )
        source.write_text("second and changed", encoding="utf-8")

        manifest, ok = bridge._load_ingest_manifest()
        files = bridge._raw_user_files()
        self.assertEqual(bridge._raw_pending_count(manifest, ok, files), 1)
        self.assertFalse(bridge._raw_index()[0]["ingested"])
        prepared = ingest_state.prepare_scan(self.vault)
        self.assertEqual(prepared["plan"]["process_paths"], ["raw/note.md"])
        self.assertEqual(prepared["plan"]["pending_items"][0]["state"], "changed")
        ingest_state.discard_plan(prepared["plan_path"])

    def test_managed_ingest_finalizes_only_after_matching_marker(self) -> None:
        (self.vault / "raw" / "new.md").write_text("new", encoding="utf-8")

        def successful_run(prompt: str, _cfg: dict) -> tuple[int, dict]:
            scan_id = re.search(r'--scan-id "([0-9a-f]+)"', prompt).group(1)
            return 200, {
                "result": f'Ingest complete\n<!-- sb:ingest-complete scan_id="{scan_id}" -->',
                "is_error": False,
            }

        with mock.patch.object(bridge, "run_skill", side_effect=successful_run):
            envelope = self.handler._run_ingest({})

        manifest, ok = ingest_state.load_manifest(self.vault)
        self.assertEqual(envelope["__status__"], 200)
        self.assertFalse(envelope["is_error"])
        self.assertTrue(ok)
        self.assertIn("fingerprint", manifest["raw/new.md"])

    def test_missing_completion_marker_leaves_source_pending(self) -> None:
        (self.vault / "raw" / "new.md").write_text("new", encoding="utf-8")
        with mock.patch.object(
            bridge,
            "run_skill",
            return_value=(200, {"result": "partial", "is_error": False}),
        ):
            envelope = self.handler._run_ingest({})

        manifest, ok = ingest_state.load_manifest(self.vault)
        self.assertTrue(envelope["is_error"])
        self.assertFalse(ok)
        self.assertNotIn("raw/new.md", manifest)

    def test_metadata_only_change_avoids_agent_call(self) -> None:
        source = self.vault / "raw" / "note.md"
        source.write_text("same", encoding="utf-8")
        entry = {
            "last_modified": "2026-01-01T00:00:00Z",
            "ingested_at": "2026-01-02T00:00:00Z",
            "fingerprint": ingest_state.fingerprint_file(source, use_cache=False),
        }
        (self.vault / "raw" / ".ingest-manifest.json").write_text(
            json.dumps({"raw/note.md": entry}), encoding="utf-8"
        )
        source.touch()

        with mock.patch.object(bridge, "run_skill") as run_skill:
            envelope = self.handler._run_ingest({})

        run_skill.assert_not_called()
        self.assertFalse(envelope["is_error"])
        self.assertIn("Nothing to ingest", envelope["result"])


if __name__ == "__main__":
    unittest.main()
