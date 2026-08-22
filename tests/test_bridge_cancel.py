from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import bridge  # noqa: E402
import ingest_state  # noqa: E402


class RunCaptureCancelTests(unittest.TestCase):
    """The cancellable-spawn primitive that backs POST /cancel."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        self.p = mock.patch.object(bridge, "VAULT_ROOT", self.vault)
        self.p.start()
        bridge._clear_proc()  # start from a clean registry

    def tearDown(self) -> None:
        bridge._clear_proc()
        self.p.stop()
        self.temp.cleanup()

    def test_request_cancel_is_a_noop_when_idle(self) -> None:
        bridge._clear_proc()
        self.assertFalse(bridge._request_cancel())

    def test_run_capture_ok_returns_completed_process(self) -> None:
        cp, outcome = bridge._run_capture(
            [sys.executable, "-c", "print('hi')"], timeout=10
        )
        self.assertEqual(outcome, "ok")
        self.assertIsNotNone(cp)
        self.assertEqual(cp.returncode, 0)
        self.assertIn("hi", cp.stdout)
        # The handle is cleared once the run ends, so a later cancel is a no-op.
        self.assertFalse(bridge._request_cancel())

    def test_run_capture_timeout_kills_child(self) -> None:
        started = time.time()
        cp, outcome = bridge._run_capture(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5
        )
        self.assertEqual(outcome, "timeout")
        self.assertIsNone(cp)
        # The child was killed rather than awaited for its full 30s sleep.
        self.assertLess(time.time() - started, 20)

    def test_request_cancel_stops_an_in_flight_run(self) -> None:
        result: dict = {}

        def worker() -> None:
            cp, outcome = bridge._run_capture(
                [sys.executable, "-c", "import time; time.sleep(30)"], timeout=60
            )
            result["cp"] = cp
            result["outcome"] = outcome

        t = threading.Thread(target=worker)
        started = time.time()
        t.start()
        # Wait for the child to register, then cancel it.
        for _ in range(500):
            if bridge._current_proc is not None:
                break
            time.sleep(0.01)
        self.assertIsNotNone(bridge._current_proc)
        self.assertTrue(bridge._request_cancel())

        t.join(timeout=15)
        self.assertFalse(t.is_alive())
        self.assertEqual(result.get("outcome"), "stopped")
        self.assertIsNone(result.get("cp"))
        # Killed promptly, not left to run out its 60s timeout.
        self.assertLess(time.time() - started, 20)


class IngestStopTests(unittest.TestCase):
    """A stopped ingest must NOT finalize — the manifest stays un-advanced so the
    next ingest re-synthesises the pending sources (self-heal)."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        for d in ("raw", "dashboard", "wiki", "outputs"):
            (self.vault / d).mkdir()
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

    def test_stopped_ingest_does_not_finalize_and_leaves_source_pending(self) -> None:
        (self.vault / "raw" / "new.md").write_text("new", encoding="utf-8")

        def stopped_run(_prompt: str, _cfg: dict) -> tuple[int, dict]:
            return 200, {"stopped": True}

        with mock.patch.object(bridge, "run_skill", side_effect=stopped_run), \
             mock.patch.object(
                 ingest_state, "finalize_plan",
                 side_effect=AssertionError("finalize_plan must not run on a stopped ingest"),
             ):
            envelope = self.handler._run_ingest({})

        self.assertEqual(envelope["__status__"], 200)
        self.assertTrue(envelope["stopped"])
        self.assertIsNone(envelope["output_file"])

        manifest, ok = ingest_state.load_manifest(self.vault)
        # Never recorded → still pending for the next ingest.
        self.assertNotIn("raw/new.md", manifest)


if __name__ == "__main__":
    unittest.main()
