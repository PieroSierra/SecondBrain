# Quickstart: Local MCP Server

1. Start the bridge as usual (`python3 dashboard/bridge.py` or the macOS app). The bridge creates `MCP_TOKEN` in `.env` on first start.
2. In the dashboard, open **Connect an agent** in the status strip. Copy the command for your client.
3. Claude Code: run the copied `claude mcp add …` command, then run `claude mcp list` and confirm `second-brain` is connected.
4. Claude Desktop: paste the copied JSON into `~/Library/Application Support/Claude/claude_desktop_config.json` under `mcpServers`, then restart Claude Desktop. This needs Node.js for `npx mcp-remote`.

## Verify

- Ask the agent: "Using second-brain, what topics do you have?" It calls `sb_list_topics`.
- Ask a question covered by one article. The answer cites the slug.
- Say "capture a summary of this conversation to second-brain". A file appears in `raw/mcp/`, and the dashboard shows one more note ready to ingest.
- Rotate the token in the panel. The next agent call fails until you re-run setup.

## Manual protocol check

```bash
curl -s http://127.0.0.1:4173/mcp -H "Authorization: Bearer $MCP_TOKEN" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: server/discover' -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```
