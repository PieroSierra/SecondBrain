# Implementation Plan: Local MCP Server

**Branch**: `003-mcp-server` | **Date**: 2026-11-27 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/003-mcp-server/spec.md`

## Summary

The bridge serves an MCP endpoint at `POST /mcp` on its existing loopback port. Local agents authenticate with a bearer token kept in `.env`. Four tools read the wiki (`sb_list_topics`, `sb_search`, `sb_read_article`) and capture content into `raw/mcp/` (`sb_capture`). Articles are also resources, and one prompt (`sb_answer`) carries the query skill's answering rules. No tool runs a model or takes the long-operation lock. The server speaks MCP `2026-07-28` and the legacy `initialize`-based revisions on the same endpoint. The dashboard gets a connection panel with setup commands and token rotation.

## Technical Context

**Language/Version**: Python 3 stdlib (bridge and new module); vanilla JS/HTML/CSS (dashboard panel)
**Primary Dependencies**: None new. Clients: Claude Code (HTTP transport), Claude Desktop through `mcp-remote` (Node.js, user-installed)
**Storage**: `wiki/` read-only, new files in `raw/mcp/`, `MCP_TOKEN` in `.env`
**Testing**: `unittest` in `tests/test_mcp_server.py` (run with `python3 -m unittest discover tests`)
**Target Platform**: macOS, single user, `127.0.0.1`
**Project Type**: Local web app (existing bridge + dashboard)
**Performance Goals**: Read tools under 1 s on 200 articles (SC-002)
**Constraints**: Stdlib only; loopback only; no writes outside `raw/mcp/` and `.env`; no `claude -p` from MCP
**Scale/Scope**: One user, a handful of local clients

## Constitution Check

No active constitution (template placeholders only). No gates.

## Architecture

```
Claude Code ──HTTP──┐
                    ├─► bridge.py  POST /mcp ─► mcp_server.handle(request) ─► wiki/ (read)
mcp-remote ──HTTP───┘              GET|DELETE /mcp → 405                  └► raw/mcp/ (create-only)
Dashboard ──────────► GET /mcp/config, POST /mcp/rotate-token (X-Bridge-Token)
```

`mcp_server.py` receives a small request object (method, headers, body bytes) and returns `(status, headers, body)`. It depends on `bridge` only through injected callables for search and paths, so tests can point it at a temporary vault.

## Project Structure

### Documentation

```text
specs/003-mcp-server/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/mcp-endpoint.md
└── tasks.md
```

### Source Code

```text
dashboard/
├── mcp_server.py      # NEW: protocol handling, tools, resources, prompt, capture writer
├── bridge.py          # route /mcp and /mcp/*; load or create MCP_TOKEN; log calls
├── index.html         # "Connect an agent" pill and modal
├── app.js             # modal logic: fetch config, copy, rotate
└── styles.css         # modal styles
tests/
└── test_mcp_server.py # NEW
dashboard/README.md    # "Connect an agent" section
```

**Structure Decision**: One new module beside `ingest_state.py`, wired into the existing bridge and dashboard.

## Design Decisions

See [research.md](research.md): dual-era protocol (R1, R2), JSON responses only (R3), per-era result fields (R4), bearer token in `.env` (R5), reject any `Origin` (R6), separate module (R7), rules read from the query skill (R8), exclusive-create capture (R9), client commands (R10).

## Complexity Tracking

| Decision | Why | Simpler alternative rejected |
|---|---|---|
| Dual-era protocol | Installed clients speak either era | Single era breaks one client population |
| Separate module | Testable without a server; keeps bridge growth small | Inline in `bridge.py` |
