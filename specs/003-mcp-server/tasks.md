# Tasks: Local MCP Server

**Input**: Design documents from `specs/003-mcp-server/`
**Tests**: Included (`tests/test_mcp_server.py`), matching existing bridge test practice.

## Phase 1: Setup

- [x] T001 Create `dashboard/mcp_server.py` with module docstring, constants (supported versions, size limits, slug pattern), and the `handle(method, headers, body)` entry point returning `(status, headers, body)`

## Phase 2: Foundational (blocks all stories)

- [x] T002 Implement the gate in `dashboard/mcp_server.py`: Host check, Origin rejection, bearer token compare, body size and JSON parsing, request/notification shape (contract: Gate)
- [x] T003 Implement era selection and modern header validation (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` with Base64 sentinel decoding), `server/discover`, `initialize`, `ping`, and per-era result decoration in `dashboard/mcp_server.py`
- [x] T004 Load or create `MCP_TOKEN` at bridge start, route `POST|GET|DELETE /mcp` to `mcp_server.handle`, and log each call in `dashboard/bridge.py`
- [x] T005 [P] Tests for gate, era selection, and header validation in `tests/test_mcp_server.py`

## Phase 3: User Story 1 - Wiki as context (P1)

- [x] T006 [US1] Implement `sb_list_topics` (parse `wiki/INDEX.md`), `sb_search` (wiki-only hits from the injected search), `sb_read_article` (slug validation, Sources footer extraction) in `dashboard/mcp_server.py`
- [x] T007 [US1] Implement `resources/list`, `resources/templates/list`, `resources/read` for `sb://wiki/<slug>` in `dashboard/mcp_server.py`
- [x] T008 [US1] Write the server `instructions` text in `dashboard/mcp_server.py`
- [x] T009 [P] [US1] Tests for list, search, read, resources, path-traversal rejection in `tests/test_mcp_server.py`

## Phase 4: User Story 2 - Capture (P1)

- [x] T010 [US2] Implement `sb_capture` with validation, exclusive-create naming, and provenance frontmatter writing to `raw/mcp/` in `dashboard/mcp_server.py`
- [x] T011 [US2] Add `mcp` to the raw subdirectory list in `dashboard/bridge.py` so captures display with their own label
- [x] T012 [P] [US2] Tests for capture naming, suffixing, frontmatter, validation, and no overwrite in `tests/test_mcp_server.py`

## Phase 5: User Story 3 - `sb_answer` prompt (P2)

- [x] T013 [US3] Implement `prompts/list` and `prompts/get` reading Step 5 of `.claude/skills/second-brain-query/SKILL.md` in `dashboard/mcp_server.py`
- [x] T014 [P] [US3] Tests for prompt extraction and fallback in `tests/test_mcp_server.py`

## Phase 6: User Story 4 - Connection panel (P2)

- [x] T015 [US4] Add `GET /mcp/config` and `POST /mcp/rotate-token` (dashboard token only) in `dashboard/bridge.py`
- [x] T016 [US4] Add the "Connect an agent" pill and modal in `dashboard/index.html`, its logic in `dashboard/app.js`, and styles in `dashboard/styles.css`
- [x] T017 [P] [US4] Tests for token persistence and rotation in `tests/test_mcp_server.py`

## Phase 7: Polish

- [x] T018 Document setup in `dashboard/README.md`
- [x] T019 Run the full test suite and an end-to-end check with Claude Code against a running bridge (quickstart.md)

## Dependencies

T001 → T002 → T003 → T004. Stories 1 to 3 depend on T003. Story 4 depends on T004. Stories 1 and 2 are independent of each other.
