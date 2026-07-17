from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "dashboard"))
import ingest_state  # noqa: E402


class IngestStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        (self.vault / "raw").mkdir()
        (self.vault / "dashboard").mkdir()
        ingest_state._HASH_CACHE.clear()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def raw(self, rel: str, body: bytes = b"hello") -> Path:
        path = self.vault / "raw" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def manifest(self, value: dict) -> None:
        (self.vault / "raw" / ".ingest-manifest.json").write_text(
            json.dumps(value), encoding="utf-8"
        )

    @staticmethod
    def legacy_entry() -> dict:
        return {
            "last_modified": "2026-01-01T00:00:00Z",
            "ingested_at": "2026-01-02T00:00:00Z",
        }

    def fingerprint_entry(self, path: Path) -> dict:
        return {
            **self.legacy_entry(),
            "fingerprint": ingest_state.fingerprint_file(path, use_cache=False),
        }

    def test_missing_manifest_marks_every_live_file_new(self) -> None:
        self.raw("new.md")
        scan = ingest_state.scan_vault(self.vault)
        self.assertEqual(scan["pending_count"], 1)
        self.assertEqual(scan["items"][0]["state"], "new")

    def test_migration_baselines_only_valid_existing_entries(self) -> None:
        old = self.raw("old.md")
        self.raw("new.md")
        self.manifest({"raw/old.md": self.legacy_entry()})

        result = ingest_state.migrate_manifest(self.vault)
        manifest, ok = ingest_state.load_manifest(self.vault)

        self.assertTrue(ok)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(
            manifest["raw/old.md"]["fingerprint"]["sha256"],
            hashlib.sha256(old.read_bytes()).hexdigest(),
        )
        self.assertNotIn("raw/new.md", manifest)
        self.assertEqual(ingest_state.scan_vault(self.vault)["pending_count"], 1)

    def test_migration_preserves_tombstones_and_invalid_entries(self) -> None:
        invalid = self.raw("invalid.md")
        original = {
            "raw/deleted.md": self.legacy_entry(),
            "raw/invalid.md": {"last_modified": "2026-01-01T00:00:00Z"},
        }
        self.manifest(original)

        ingest_state.migrate_manifest(self.vault)
        manifest, _ = ingest_state.load_manifest(self.vault)

        self.assertEqual(manifest["raw/deleted.md"], original["raw/deleted.md"])
        self.assertNotIn("fingerprint", manifest["raw/invalid.md"])
        item = ingest_state.scan_vault(self.vault)["items"][0]
        self.assertEqual(item["path"], invalid.relative_to(self.vault).as_posix())
        self.assertEqual(item["state"], "invalid")
        self.assertTrue(item["pending"])

    def test_matching_metadata_is_fast_path_without_hashing(self) -> None:
        path = self.raw("stable.md")
        self.manifest({"raw/stable.md": self.fingerprint_entry(path)})

        with mock.patch.object(
            ingest_state, "fingerprint_file", side_effect=AssertionError("should not hash")
        ):
            scan = ingest_state.scan_vault(self.vault)

        self.assertEqual(scan["pending_count"], 0)
        self.assertEqual(scan["items"][0]["state"], "current")

    def test_touch_only_is_metadata_change_not_pending(self) -> None:
        path = self.raw("touched.md")
        entry = self.fingerprint_entry(path)
        self.manifest({"raw/touched.md": entry})
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 5_000_000_000))

        scan = ingest_state.scan_vault(self.vault)
        self.assertEqual(scan["pending_count"], 0)
        self.assertEqual(scan["items"][0]["state"], "metadata_only")

        prepared = ingest_state.prepare_scan(self.vault)
        manifest, _ = ingest_state.load_manifest(self.vault)
        self.assertEqual(prepared["plan"]["process_paths"], [])
        self.assertEqual(
            manifest["raw/touched.md"]["fingerprint"]["mtime_ns"],
            path.stat().st_mtime_ns,
        )
        ingest_state.discard_plan(prepared["plan_path"])

    def test_content_edit_becomes_pending(self) -> None:
        path = self.raw("edited.md", b"before")
        self.manifest({"raw/edited.md": self.fingerprint_entry(path)})
        path.write_bytes(b"after with changed size")

        scan = ingest_state.scan_vault(self.vault)
        self.assertEqual(scan["pending_count"], 1)
        self.assertEqual(scan["items"][0]["state"], "changed")

    def test_unreadable_or_unstable_source_stays_pending(self) -> None:
        self.raw("unstable.md")
        with mock.patch.object(
            ingest_state, "fingerprint_file", side_effect=ingest_state.SourceChanged("changed")
        ):
            item = ingest_state.scan_vault(self.vault)["items"][0]
        self.assertTrue(item["pending"])
        self.assertFalse(item["processable"])
        self.assertEqual(item["state"], "changed_during_scan")

    def test_incomplete_pdf_markdown_stays_pending_but_not_processable(self) -> None:
        self.raw("pdf/partial.md", b"partial\n<!-- sb:incomplete -->\n")
        item = ingest_state.scan_vault(self.vault)["items"][0]
        self.assertEqual(item["state"], "incomplete")
        self.assertTrue(item["pending"])
        self.assertFalse(item["processable"])

    def test_finalize_records_unchanged_processed_source(self) -> None:
        path = self.raw("new.md")
        prepared = ingest_state.prepare_scan(self.vault)
        result = ingest_state.finalize_plan(self.vault, prepared["plan_path"])
        manifest, ok = ingest_state.load_manifest(self.vault)

        self.assertTrue(ok)
        self.assertEqual(result["finalized"], ["raw/new.md"])
        self.assertEqual(result["changed_during_ingest"], [])
        self.assertEqual(
            manifest["raw/new.md"]["fingerprint"]["sha256"],
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        self.assertEqual(ingest_state.scan_vault(self.vault)["pending_count"], 0)

    def test_finalize_refuses_source_changed_during_ingest(self) -> None:
        path = self.raw("new.md", b"first")
        prepared = ingest_state.prepare_scan(self.vault)
        path.write_bytes(b"second and different")

        result = ingest_state.finalize_plan(self.vault, prepared["plan_path"])
        manifest, ok = ingest_state.load_manifest(self.vault)

        self.assertEqual(result["finalized"], [])
        self.assertEqual(result["changed_during_ingest"], ["raw/new.md"])
        self.assertFalse(ok)
        self.assertNotIn("raw/new.md", manifest)

    def test_streaming_hash_matches_hashlib(self) -> None:
        body = (b"0123456789abcdef" * (ingest_state.HASH_CHUNK_BYTES // 8)) + b"tail"
        path = self.raw("large.md", body)
        fp = ingest_state.fingerprint_file(path, use_cache=False)
        self.assertEqual(fp["sha256"], hashlib.sha256(body).hexdigest())

    def test_parallel_prepare_calls_leave_valid_manifest(self) -> None:
        for i in range(8):
            path = self.raw(f"{i}.md", str(i).encode())
            if i == 0:
                self.manifest({"raw/0.md": self.legacy_entry()})

        errors: list[Exception] = []
        plans: list[Path] = []

        def worker() -> None:
            try:
                result = ingest_state.prepare_scan(self.vault)
                plans.append(result["plan_path"])
            except Exception as exc:  # pragma: no cover - assertion reports it
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        manifest, ok = ingest_state.load_manifest(self.vault)
        self.assertTrue(ok)
        self.assertIn("fingerprint", manifest["raw/0.md"])
        self.assertEqual(list((self.vault / "raw").glob(".*.tmp")), [])
        for plan in plans:
            ingest_state.discard_plan(plan)

    def test_direct_cli_prepare_and_finalize_use_same_state_engine(self) -> None:
        self.raw("cli.md", b"from cli")
        prepare_out = io.StringIO()
        finalize_out = io.StringIO()
        with mock.patch.object(ingest_state, "_default_vault_root", return_value=self.vault):
            with redirect_stdout(prepare_out):
                self.assertEqual(ingest_state.main(["prepare"]), 0)
            prepared = json.loads(prepare_out.getvalue())
            self.assertEqual(prepared["pending"], 1)
            self.assertEqual(prepared["processable"], 1)

            with redirect_stdout(finalize_out):
                self.assertEqual(ingest_state.main(["finalize"]), 0)

        finalized = json.loads(finalize_out.getvalue())
        self.assertEqual(finalized["finalized"], ["raw/cli.md"])
        manifest, ok = ingest_state.load_manifest(self.vault)
        self.assertTrue(ok)
        self.assertIn("fingerprint", manifest["raw/cli.md"])


if __name__ == "__main__":
    unittest.main()
