# Research: Local MCP Server

## R1. Protocol revision

**Decision**: Implement a dual-era server. Serve modern revision `2026-07-28` (stateless, per-request `_meta`, `server/discover`) and the legacy revisions `2025-11-25`, `2025-06-18`, and `2025-03-26` (an `initialize` handshake).

**Rationale**: `2026-07-28` removed the `initialize` handshake and protocol sessions. Installed clients (Claude Code, `mcp-remote`) may speak either era. The spec's compatibility matrix says a dual-era server works with modern, dual-era, and legacy clients. A modern-only server fails with legacy clients, which have no fall-forward path.

**Alternatives considered**: Modern only (breaks older clients). Legacy only (breaks modern-only clients over time).

## R2. Era detection on one endpoint

**Decision**: Select the era per request:

- A request whose `params._meta` carries `io.modelcontextprotocol/protocolVersion` is modern. The server validates `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` headers against the body and returns `400` with `HeaderMismatch` (`-32020`) on a mismatch, `400` with `UnsupportedProtocolVersionError` (`-32022`) for an unknown version, and `404` with `-32601` for an unknown method.
- `initialize` selects legacy semantics. Later legacy requests carry `MCP-Protocol-Version` (2025-06-18 and later) or no header (2025-03-26). The server runs them statelessly. It mints no `Mcp-Session-Id`, which the 2025-11-25 transport allows.
- `notifications/initialized` and other notifications return `202` with no body.
- `GET` and `DELETE` on `/mcp` return `405`.

**Rationale**: Statelessness in both eras avoids session bookkeeping. The 2025-11-25 transport makes session IDs optional for servers, and it allows `405` for `GET` when the server offers no standalone SSE stream.

## R3. Response format

**Decision**: Always respond with `Content-Type: application/json`. No SSE.

**Rationale**: Every tool finishes in milliseconds. Both eras allow a JSON response to a POST request.

## R4. Result fields per era

**Decision**: Modern results carry `resultType: "complete"` and `_meta["io.modelcontextprotocol/serverInfo"]`. Modern list results and `resources/read` also carry `ttlMs` and `cacheScope: "private"`. Legacy results omit these fields. A missing resource returns `-32602` in modern and `-32002` in legacy.

## R5. Authentication

**Decision**: A static bearer token in `Authorization: Bearer <token>`. It is stored as `MCP_TOKEN` in the vault-root `.env`, which is gitignored. The bridge generates it on first start. Writes go through `_write_env_var`, which creates the file from `mkstemp` (mode 0600). A request without the token gets `401` with a JSON-RPC error body and no `WWW-Authenticate` header, so clients do not start an OAuth discovery flow.

**Rationale**: Claude Code (`--header`) and `mcp-remote` (`--header`) both send static headers. OAuth is out of scope.

## R6. Origin and Host

**Decision**: Reject any request whose `Host` is not `localhost` or `127.0.0.1`. Reject any request that carries an `Origin` header with `403`.

**Rationale**: The spec requires Origin validation. The supported clients are not browsers and send no `Origin`. Rejecting every Origin blocks browser pages, including the Chrome extension, from reaching the endpoint.

## R7. Code placement

**Decision**: A new module `dashboard/mcp_server.py` holds the protocol and tool logic as plain functions over a request object. `bridge.py` routes `POST|GET|DELETE /mcp` to it and adds two dashboard endpoints for the connection panel.

**Rationale**: `bridge.py` is 4,400 lines. A separate module is testable with `unittest` without starting a server, matching `ingest_state.py`.

## R8. `sb_answer` rule source

**Decision**: Read `.claude/skills/second-brain-query/SKILL.md` on each `prompts/get` call and extract the `### Step 5` section up to `### Step 6`. Drop lines that mention writing an output file or another step. Fall back to the short rules if the section is missing.

**Rationale**: FR-008 requires the rules to come from the skill's current definition.

## R9. Capture write

**Decision**: Open the target with mode `"x"` (exclusive create). On `FileExistsError`, try the next numeric suffix. Frontmatter values are single-line and quoted when needed. The body is written unchanged.

**Rationale**: Exclusive create makes FR-018 hold under concurrent captures.

## R10. Client setup commands

**Decision**:

- Claude Code: `claude mcp add --transport http second-brain http://127.0.0.1:<port>/mcp --header "Authorization: Bearer <token>"`
- Claude Desktop: a `claude_desktop_config.json` entry that runs `npx -y mcp-remote http://127.0.0.1:<port>/mcp --header "Authorization: Bearer <token>"`.
