from __future__ import annotations

import base64
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import bridge  # noqa: E402
import mcp_server  # noqa: E402

TOKEN = "test-token"
MODERN = mcp_server.MODERN_VERSION


def _slugify(text: str) -> str:
    return bridge._slugify(text)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        wiki = self.root / "wiki"
        wiki.mkdir()
        (wiki / "INDEX.md").write_text(
            "# Knowledge Base Index\n\n"
            "| Topic | Summary | Last Updated |\n"
            "|-------|---------|--------------|\n"
            "| [[ai-product]] | AI product summary. | 2026-11-26 |\n"
            "| [[ai-partnerships]] | Partnerships summary. | 2026-11-25 |\n",
            encoding="utf-8",
        )
        (wiki / "ai-product.md").write_text(
            "# AI Product\n\nAI Answers & Actions is the product frame.\n\n"
            "*Sources: [[raw/a.md]] (2026-01-01)*\n"
            "*Sources (continued): [[raw/b.md]] (2026-02-01)*\n",
            encoding="utf-8",
        )
        (wiki / "ai-partnerships.md").write_text(
            "# AI Partnerships\n\nOpenAI conversations continue.\n", encoding="utf-8"
        )
        (self.root / "secret.md").write_text("outside wiki", encoding="utf-8")
        self.searched: list[str] = []

        def search(q: str) -> list:
            self.searched.append(q)
            return [
                {"source": "wiki", "id": "ai-product", "title": "AI Product", "snippet": "…AI…", "hits": 3},
                {"source": "output", "id": "2026-01-01_query-x.md", "title": "X", "snippet": "…", "hits": 9},
            ]

        self.vault = mcp_server.Vault(root=self.root, search=search, slugify=_slugify)

    def tearDown(self) -> None:
        self.temp.cleanup()

    # -- request helpers -------------------------------------------------

    def post(self, message, headers=None, token=TOKEN, raw=None):
        h = {"Host": "127.0.0.1:4173", "Authorization": f"Bearer {TOKEN}",
             "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        h.update(headers or {})
        body = raw if raw is not None else json.dumps(message).encode()
        return mcp_server.handle(self.vault, "POST", h, body, token)

    def modern(self, method, params=None, extra_headers=None, rid=1):
        params = dict(params or {})
        params["_meta"] = {
            mcp_server.META_VERSION: MODERN,
            mcp_server.META_CLIENT_INFO: {"name": "test-client", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        headers = {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
        named = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}.get(method)
        if named:
            headers["Mcp-Name"] = params[named]
        headers.update(extra_headers or {})
        return self.post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, headers)

    def legacy(self, method, params=None, version="2025-11-25", rid=1, session=None):
        headers = {"MCP-Protocol-Version": version} if version else {}
        if session:
            headers["Mcp-Session-Id"] = session
        return self.post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, headers)

    @staticmethod
    def body(resp):
        return json.loads(resp.body)

    def call_tool(self, name, arguments=None):
        resp = self.modern("tools/call", {"name": name, "arguments": arguments or {}})
        self.assertEqual(resp.status, 200, resp.body)
        return self.body(resp)["result"]


class GateTests(_Base):
    def test_rejects_foreign_host(self):
        resp = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Host": "evil.example:4173"})
        self.assertEqual(resp.status, 403)

    def test_rejects_any_origin(self):
        resp = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                         {"Origin": "chrome-extension://abcdefghijklmnop"})
        self.assertEqual(resp.status, 403)

    def test_rejects_missing_and_wrong_token(self):
        self.assertEqual(self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   {"Authorization": ""}).status, 401)
        self.assertEqual(self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   {"Authorization": "Bearer nope"}).status, 401)

    def test_rejects_when_no_token_configured(self):
        self.assertEqual(self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="").status, 401)

    def test_rejects_dashboard_token_header(self):
        resp = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                         {"Authorization": "", "X-Bridge-Token": bridge.BRIDGE_TOKEN})
        self.assertEqual(resp.status, 401)

    def test_get_and_delete_are_405(self):
        h = {"Host": "localhost:4173", "Authorization": f"Bearer {TOKEN}"}
        self.assertEqual(mcp_server.handle(self.vault, "GET", h, b"", TOKEN).status, 405)
        self.assertEqual(mcp_server.handle(self.vault, "DELETE", h, b"", TOKEN).status, 405)

    def test_parse_error_and_batch(self):
        self.assertEqual(self.post(None, raw=b"{not json").status, 400)
        self.assertEqual(self.post([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]).status, 400)

    def test_notification_is_202(self):
        resp = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(resp.status, 202)
        self.assertEqual(resp.body, b"")


class ModernProtocolTests(_Base):
    def test_discover(self):
        result = self.body(self.modern("server/discover"))["result"]
        self.assertIn(MODERN, result["supportedVersions"])
        self.assertEqual(result["resultType"], "complete")
        self.assertIn("cite", result["instructions"])
        self.assertEqual(result["cacheScope"], "private")
        self.assertIn(mcp_server.META_SERVER_INFO, result["_meta"])

    def test_unsupported_version(self):
        resp = self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {
            "_meta": {mcp_server.META_VERSION: "1900-01-01"}}},
            {"MCP-Protocol-Version": "1900-01-01", "Mcp-Method": "tools/list"})
        self.assertEqual(resp.status, 400)
        err = self.body(resp)["error"]
        self.assertEqual(err["code"], -32022)
        self.assertIn(MODERN, err["data"]["supported"])

    def test_header_mismatches(self):
        self.assertEqual(self.modern("tools/list", extra_headers={"Mcp-Method": "tools/call"}).status, 400)
        self.assertEqual(self.modern("tools/list", extra_headers={"MCP-Protocol-Version": "2025-11-25"}).status, 400)
        resp = self.modern("tools/call", {"name": "sb_list_topics"}, extra_headers={"Mcp-Name": "other"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.body(resp)["error"]["code"], -32020)

    def test_base64_mcp_name_is_decoded(self):
        encoded = "=?base64?" + base64.b64encode(b"sb_list_topics").decode() + "?="
        resp = self.modern("tools/call", {"name": "sb_list_topics"}, extra_headers={"Mcp-Name": encoded})
        self.assertEqual(resp.status, 200)

    def test_unknown_method_is_404(self):
        resp = self.modern("bogus/method")
        self.assertEqual(resp.status, 404)
        self.assertEqual(self.body(resp)["error"]["code"], -32601)

    def test_client_name_is_logged(self):
        resp = self.modern("tools/list")
        self.assertEqual(resp.log["client"], "test-client")


class LegacyProtocolTests(_Base):
    def test_initialize_negotiates_and_issues_session(self):
        resp = self.legacy("initialize", {"protocolVersion": "2025-06-18",
                                          "clientInfo": {"name": "claude-code", "version": "2"},
                                          "capabilities": {}}, version=None)
        result = self.body(resp)["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("instructions", result)
        self.assertNotIn("resultType", result)
        sid = resp.headers["Mcp-Session-Id"]
        listed = self.legacy("tools/list", version="2025-06-18", session=sid)
        self.assertEqual(listed.log["client"], "claude-code")

    def test_unknown_initialize_version_falls_back(self):
        resp = self.legacy("initialize", {"protocolVersion": "2099-01-01"}, version=None)
        self.assertEqual(self.body(resp)["result"]["protocolVersion"], "2025-11-25")

    def test_requests_without_session_are_served(self):
        resp = self.legacy("tools/list")
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(self.body(resp)["result"]["tools"]), 4)

    def test_headerless_request_is_2025_03_26(self):
        self.assertEqual(self.legacy("ping", version=None).status, 200)

    def test_unsupported_legacy_header(self):
        self.assertEqual(self.legacy("tools/list", version="2024-11-05").status, 400)

    def test_modern_header_without_meta_is_rejected(self):
        self.assertEqual(self.legacy("tools/list", version=MODERN).status, 400)

    def test_missing_resource_uses_legacy_code(self):
        err = self.body(self.legacy("resources/read", {"uri": "sb://wiki/nope"}))["error"]
        self.assertEqual(err["code"], -32002)


class ReadToolTests(_Base):
    def test_list_topics(self):
        result = self.call_tool("sb_list_topics")
        topics = result["structuredContent"]["topics"]
        self.assertEqual([t["slug"] for t in topics], ["ai-product", "ai-partnerships"])
        self.assertEqual(topics[0]["title"], "AI Product")
        self.assertEqual(topics[0]["summary"], "AI product summary.")
        self.assertEqual(topics[0]["updated"], "2026-11-26")

    def test_list_topics_without_index(self):
        (self.root / "wiki" / "INDEX.md").unlink()
        result = self.call_tool("sb_list_topics")
        self.assertFalse(result["structuredContent"]["indexed"])
        self.assertIn("not been ingested", result["content"][0]["text"])

    def test_search_returns_wiki_hits_only(self):
        result = self.call_tool("sb_search", {"query": " AI "})
        hits = result["structuredContent"]["results"]
        self.assertEqual([h["slug"] for h in hits], ["ai-product"])
        self.assertEqual(self.searched, ["AI"])

    def test_read_article_includes_sources(self):
        data = self.call_tool("sb_read_article", {"slug": "ai-product"})["structuredContent"]
        self.assertEqual(data["path"], "wiki/ai-product.md")
        self.assertEqual(data["uri"], "sb://wiki/ai-product")
        self.assertIn("[[raw/a.md]]", data["sources"])
        self.assertIn("[[raw/b.md]]", data["sources"])

    def test_read_article_rejects_traversal_and_missing(self):
        for slug in ("../secret", "nope", "INDEX", "a/b", "", None):
            result = self.call_tool("sb_read_article", {"slug": slug})
            self.assertTrue(result["isError"], slug)

    def test_unknown_tool_is_invalid_params(self):
        resp = self.modern("tools/call", {"name": "sb_delete", "arguments": {}})
        self.assertEqual(self.body(resp)["error"]["code"], -32602)

    def test_resources(self):
        listed = self.body(self.modern("resources/list"))["result"]["resources"]
        self.assertEqual({r["uri"] for r in listed}, {"sb://wiki/ai-product", "sb://wiki/ai-partnerships"})
        read = self.body(self.modern("resources/read", {"uri": "sb://wiki/ai-product"}))["result"]
        self.assertIn("AI Answers", read["contents"][0]["text"])
        missing = self.body(self.modern("resources/read", {"uri": "sb://wiki/../secret"}))["error"]
        self.assertEqual(missing["code"], -32602)


class CaptureTests(_Base):
    def capture(self, **args):
        return self.call_tool("sb_capture", args)

    def test_writes_new_file_with_frontmatter(self):
        result = self.capture(title="Key points: MCP design", markdown="- one\n- two",
                              conversation_url="https://claude.ai/chat/abc")
        self.assertFalse(result["isError"])
        path = result["structuredContent"]["path"]
        self.assertRegex(path, r"^raw/mcp/\d{4}-\d{2}-\d{2}_key-points-mcp-design\.md$")
        text = (self.root / path).read_text(encoding="utf-8")
        self.assertIn("source: mcp:test-client\n", text)
        self.assertIn("title: Key points: MCP design\n", text)
        self.assertIn("conversation_url: https://claude.ai/chat/abc\n", text)
        self.assertTrue(text.endswith("---\n\n- one\n- two\n"))
        self.assertIn("ingest", result["content"][0]["text"])

    def test_same_title_gets_suffix_and_keeps_original(self):
        first = self.capture(title="Note", markdown="first")["structuredContent"]["path"]
        second = self.capture(title="Note", markdown="second")["structuredContent"]["path"]
        self.assertTrue(second.endswith("_note-2.md"))
        self.assertIn("first", (self.root / first).read_text())

    def test_empty_slug_falls_back(self):
        path = self.capture(title="???", markdown="x")["structuredContent"]["path"]
        self.assertTrue(path.endswith("_capture.md"))

    def test_rejections_write_nothing(self):
        bad = [
            {"title": "", "markdown": "x"},
            {"title": "a\nb", "markdown": "x"},
            {"title": "t", "markdown": "  "},
            {"title": "t", "markdown": "x" * (256 * 1024 + 1)},
            {"title": "t", "markdown": "x", "conversation_url": "javascript:alert(1)"},
            {"title": "x" * 201, "markdown": "x"},
        ]
        for args in bad:
            self.assertTrue(self.capture(**args)["isError"], args)
        self.assertFalse((self.root / "raw" / "mcp").exists() and any((self.root / "raw" / "mcp").iterdir()))

    def test_wiki_is_untouched(self):
        before = {p.name: p.read_bytes() for p in (self.root / "wiki").iterdir()}
        self.capture(title="t", markdown="x")
        after = {p.name: p.read_bytes() for p in (self.root / "wiki").iterdir()}
        self.assertEqual(before, after)


class PromptTests(_Base):
    def write_skill(self, text: str) -> None:
        skill = self.root / ".claude" / "skills" / "second-brain-query"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(text, encoding="utf-8")

    def test_rules_come_from_query_skill(self):
        self.write_skill("### Step 4 — Read\n\nread\n\n### Step 5 — Synthesise the answer\n\n"
                         "- Use `[[topic-name]]` wikilink citations\n"
                         "Write an output file noting the gap (see Step 6) and stop.\n\n"
                         "### Step 6 — Title\n\ntitle rules\n")
        resp = self.modern("prompts/get", {"name": "sb_answer", "arguments": {"question": "What?"}})
        text = self.body(resp)["result"]["messages"][0]["content"]["text"]
        self.assertIn("wikilink citations", text)
        self.assertNotIn("output file", text)
        self.assertNotIn("title rules", text)
        self.assertTrue(text.endswith("Question: What?"))

    def test_real_query_skill_parses(self):
        vault = mcp_server.Vault(root=ROOT, search=lambda q: [], slugify=_slugify)
        rules = mcp_server.answer_rules(vault)
        self.assertNotEqual(rules, mcp_server._FALLBACK_ANSWER_RULES)
        self.assertIn("Freshness", rules)

    def test_fallback_without_skill(self):
        self.assertEqual(mcp_server.answer_rules(self.vault), mcp_server._FALLBACK_ANSWER_RULES)

    def test_missing_question(self):
        resp = self.modern("prompts/get", {"name": "sb_answer", "arguments": {}})
        self.assertEqual(self.body(resp)["error"]["code"], -32602)

    def test_prompts_list(self):
        prompts = self.body(self.modern("prompts/list"))["result"]["prompts"]
        self.assertEqual(prompts[0]["name"], "sb_answer")


class TokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        self.patches = [mock.patch.object(bridge, "VAULT_ROOT", self.vault),
                        mock.patch.object(bridge, "_MCP_TOKEN", "")]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def test_creates_and_persists_token_privately(self):
        bridge._ensure_mcp_token()
        token = bridge._MCP_TOKEN
        self.assertTrue(token)
        env = self.vault / ".env"
        self.assertIn(f"MCP_TOKEN={token}\n", env.read_text())
        self.assertEqual(stat.S_IMODE(os.stat(env).st_mode), 0o600)

    def test_existing_token_is_kept(self):
        bridge._MCP_TOKEN = "kept"
        bridge._ensure_mcp_token()
        self.assertEqual(bridge._MCP_TOKEN, "kept")
        self.assertFalse((self.vault / ".env").exists())

    def test_rotation_replaces_token(self):
        bridge._ensure_mcp_token()
        old = bridge._MCP_TOKEN
        bridge._rotate_mcp_token()
        self.assertNotEqual(old, bridge._MCP_TOKEN)
        text = (self.vault / ".env").read_text()
        self.assertNotIn(old, text)
        self.assertEqual(text.count("MCP_TOKEN="), 1)

    def test_config_contains_commands(self):
        bridge._MCP_TOKEN = "abc"
        cfg = bridge._mcp_config()
        self.assertIn("Authorization: Bearer abc", cfg["claude_code"])
        self.assertEqual(cfg["claude_desktop"]["mcpServers"]["second-brain"]["args"][-1],
                         "Authorization: Bearer abc")


if __name__ == "__main__":
    unittest.main()
