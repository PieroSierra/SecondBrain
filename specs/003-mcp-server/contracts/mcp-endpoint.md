# Contract: MCP Endpoint and Connection API

## `POST /mcp`

JSON-RPC 2.0 over Streamable HTTP. One request or notification per POST. Responses are `application/json`.

### Gate (both eras, checked in order)

| Check | Failure |
|---|---|
| `Host` is `localhost` or `127.0.0.1` (any port) | `403`, JSON-RPC error `-32600`, no `id` |
| No `Origin` header | `403`, JSON-RPC error `-32600`, no `id` |
| `Authorization: Bearer <MCP_TOKEN>` (constant-time compare) | `401`, JSON-RPC error `-32600` "unauthorized" |
| Body under 512 KB and valid JSON object | `400`, `-32700` parse error |
| Body is a request or notification, not a batch or response | `400`, `-32600` |

### Era selection

- Notification (no `id`): `202`, empty body.
- `params._meta["io.modelcontextprotocol/protocolVersion"]` present: modern.
- Method `initialize`: legacy handshake.
- Otherwise: legacy, version from `MCP-Protocol-Version` or `2025-03-26` when absent.

### Modern validation (`2026-07-28`)

| Condition | Response |
|---|---|
| Version not `2026-07-28` | `400`, `-32022`, `data: {supported, requested}` |
| `MCP-Protocol-Version` missing or differs from `_meta` | `400`, `-32020` |
| `Mcp-Method` missing or differs from `method` | `400`, `-32020` |
| `Mcp-Name` missing or differs from `params.name`/`params.uri` on `tools/call`, `resources/read`, `prompts/get` (Base64 sentinel decoded first) | `400`, `-32020` |
| Unknown method | `404`, `-32601` |

### Legacy versions

`2025-11-25`, `2025-06-18`, `2025-03-26`. `initialize` echoes the client's version when supported, else `2025-11-25`. An unsupported `MCP-Protocol-Version` header on a later request returns `400`. Unknown method returns `200` with `-32601`. No `Mcp-Session-Id` is issued.

### Methods

| Method | Era | Result |
|---|---|---|
| `server/discover` | modern | `supportedVersions`, `capabilities`, `instructions`, serverInfo |
| `initialize` | legacy | `protocolVersion`, `capabilities`, `serverInfo`, `instructions` |
| `ping` | legacy | `{}` |
| `tools/list` | both | four tools |
| `tools/call` | both | `content` (one text block), `structuredContent`, `isError` |
| `resources/list` | both | one resource per article |
| `resources/read` | both | `contents: [{uri, mimeType: "text/markdown", text}]` |
| `resources/templates/list` | both | `sb://wiki/{slug}` |
| `prompts/list` | both | `sb_answer` |
| `prompts/get` | both | one user message with rules and question |

Capabilities: `{"tools": {}, "resources": {}, "prompts": {}}`.

Modern results add `resultType: "complete"` and `_meta["io.modelcontextprotocol/serverInfo"]`. Modern list results and `resources/read` add `ttlMs: 0` and `cacheScope: "private"`.

### Tools

| Tool | Input | `structuredContent` |
|---|---|---|
| `sb_list_topics` | none | `{topics: [{slug, title, summary, updated}], indexed: bool}` |
| `sb_search` | `{query: string}` | `{query, results: [{slug, title, snippet, hits}]}` |
| `sb_read_article` | `{slug: string}` | `{slug, path, uri, title, sources, text}` |
| `sb_capture` | `{title: string, markdown: string, conversation_url?: string}` | `{path, note}` |

Tool errors (bad slug, missing article, rejected capture) return a normal result with `isError: true` and a text message.

## Dashboard endpoints (dashboard token only, not the extension Origin)

### `GET /mcp/config`

```json
{"endpoint": "http://127.0.0.1:4173/mcp", "token": "…", "claude_code": "claude mcp add …", "claude_desktop": {"mcpServers": {"second-brain": {"command": "npx", "args": ["-y", "mcp-remote", "…", "--header", "Authorization: Bearer …"]}}}}
```

### `POST /mcp/rotate-token`

Generates and persists a new token. Returns the same body as `GET /mcp/config`.
