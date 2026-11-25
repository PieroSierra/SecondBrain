from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import bridge  # noqa: E402


class ParseOpencodeModelsTests(unittest.TestCase):
    def test_keeps_provider_model_lines_in_order(self) -> None:
        out = "portkey/gpt-5.6-sol\nportkey/claude-sonnet-5-5\n"
        self.assertEqual(
            bridge._parse_opencode_models(out),
            ["portkey/gpt-5.6-sol", "portkey/claude-sonnet-5-5"],
        )

    def test_ignores_blank_and_non_model_lines(self) -> None:
        out = "\nINFO refreshing cache\nopenai/gpt-6-sol\n  \nnot a model\n"
        self.assertEqual(bridge._parse_opencode_models(out), ["openai/gpt-6-sol"])

    def test_drops_duplicates(self) -> None:
        out = "openai/gpt-6-sol\nopenai/gpt-6-sol\n"
        self.assertEqual(bridge._parse_opencode_models(out), ["openai/gpt-6-sol"])


class DiscoverOpencodeModelsTests(unittest.TestCase):
    def test_missing_binary_returns_empty_list(self) -> None:
        with mock.patch.object(bridge, "OPENCODE_BIN", "/nonexistent/opencode"):
            self.assertEqual(bridge._discover_opencode_models(), [])


class ApplyOpencodeModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.map_patch = mock.patch.object(
            bridge, "_OPENCODE_TIER_MAP", {"openai/gpt-6-sol": "openai/gpt-6-sol"}
        )
        self.map_patch.start()

    def tearDown(self) -> None:
        self.map_patch.stop()

    def test_discovered_models_replace_the_built_in_map(self) -> None:
        with mock.patch.object(bridge, "_OPENCODE_MODEL_TIER", "default"):
            bridge._apply_opencode_models(["portkey/gpt-5.6-sol"])
            self.assertEqual(
                bridge._OPENCODE_TIER_MAP, {"portkey/gpt-5.6-sol": "portkey/gpt-5.6-sol"}
            )

    def test_unavailable_saved_model_resets_to_default(self) -> None:
        with mock.patch.object(bridge, "_OPENCODE_MODEL_TIER", "openai/gpt-6-luna"):
            bridge._apply_opencode_models(["portkey/gpt-5.6-sol"])
            self.assertEqual(bridge._OPENCODE_MODEL_TIER, "default")

    def test_available_saved_model_is_kept(self) -> None:
        with mock.patch.object(bridge, "_OPENCODE_MODEL_TIER", "portkey/gpt-5.6-sol"):
            bridge._apply_opencode_models(["portkey/gpt-5.6-sol"])
            self.assertEqual(bridge._OPENCODE_MODEL_TIER, "portkey/gpt-5.6-sol")

    def test_empty_discovery_keeps_built_in_map_and_saved_model(self) -> None:
        with mock.patch.object(bridge, "_OPENCODE_MODEL_TIER", "openai/gpt-6-sol"):
            bridge._apply_opencode_models([])
            self.assertEqual(
                bridge._OPENCODE_TIER_MAP, {"openai/gpt-6-sol": "openai/gpt-6-sol"}
            )
            self.assertEqual(bridge._OPENCODE_MODEL_TIER, "openai/gpt-6-sol")


class RunOpencodeArgvTests(unittest.TestCase):
    def test_does_not_pass_dir_flag(self) -> None:
        # OpenCode V2 rejects --dir; the vault is set as the process cwd instead.
        captured: list[list[str]] = []

        def fake_run_capture(argv, timeout):
            captured.append(argv)
            return mock.Mock(stdout="", stderr="", returncode=0), "ok"

        with mock.patch.object(bridge, "_run_capture", fake_run_capture):
            bridge.run_opencode("hi", {"timeout": 5})
        self.assertNotIn("--dir", captured[0])


if __name__ == "__main__":
    unittest.main()
