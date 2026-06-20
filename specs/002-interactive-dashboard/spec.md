# Feature Specification: Interactive KB Dashboard

**Feature Branch**: `002-interactive-dashboard`
**Created**: 2026-06-16
**Status**: Draft
**Input**: User description: "An attractive local web dashboard for the Second Brain KB. The homepage hero is a query box that answers questions from the KB. Secondary commands import a PDF (browse + ingest), import from Craft (folder + doc name), and paste-import Markdown, plus commands to ingest raw → wiki and to lint the wiki. Browsing the wiki as a website is a planned future feature, not built in this iteration."

## Summary

Give the existing skills-driven Second Brain a single, attractive local web front-end so the owner can run the most common KB operations from a browser instead of typing slash commands in a terminal. The home view leads with a prominent natural-language **query box** that returns a readable answer from the wiki. Below the hero, a small set of **commands** cover getting content in (paste Markdown, import a PDF, import from Craft) and keeping the wiki current (ingest, lint). The dashboard adds no new knowledge-base logic — it is a presentation and orchestration layer over the skills that already exist (`second-brain-query`, `second-brain-md-add`, `second-brain-pdf-import`, `second-brain-craft-import`, `second-brain-ingest`, `second-brain-lint`).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Ask the knowledge base a question (Priority: P1)

The owner opens the dashboard and types a natural-language question into the hero query box. A readable answer drawn from the wiki appears in the page, without ever opening a terminal.

**Why this priority**: Fast, friction-free retrieval is the headline reason to leave the CLI. The dashboard delivers its core value the moment this works, even if no other command is built yet.

**Independent Test**: Against the already-populated vault, open the dashboard, submit a question, and confirm a correct, rendered answer is displayed in the page.

**Acceptance Scenarios**:

1. **Given** the dashboard is open, **When** the owner submits a question, **Then** a visible in-progress state appears and is then replaced by the answer rendered as formatted Markdown (not raw source).
2. **Given** a query is running, **When** it completes, **Then** the answer is shown in the page itself, not merely written to a file the owner has to go find.
3. **Given** a query completes successfully, **When** the answer is displayed, **Then** a link to the saved `outputs/YYYY-MM-DD_query-*.md` file is also shown so the owner can open or reuse it.
4. **Given** a query fails or times out, **When** the error occurs, **Then** a clear failure message is shown rather than a blank or indefinitely spinning UI.

---

### User Story 2 — Add new content (Priority: P2)

The owner captures new source material into `raw/` from the dashboard via three paths — paste Markdown, select a PDF, or pull a named Craft document — so capture no longer requires the terminal.

**Why this priority**: Feeding the KB keeps it useful, but the underlying skills already work; the dashboard makes capture easier rather than enabling something new. Valuable, but secondary to retrieval.

**Independent Test**: Exercise each of the three import controls and confirm a corresponding file lands in the correct `raw/` subfolder with an on-screen confirmation.

**Acceptance Scenarios**:

1. **Given** the owner pastes Markdown and submits, **Then** a new file is created in `raw/` and the dashboard confirms it, naming the created file.
2. **Given** the owner selects a PDF from anywhere on the local machine, **Then** it is imported into `raw/pdf/` and the dashboard confirms success.
3. **Given** the owner supplies a Craft folder and document name, **Then** that document is imported into `raw/craft/` and the dashboard confirms success.
4. **Given** any import fails (e.g. Craft document not found, unreadable PDF), **Then** a clear, specific error is shown.
5. **Given** a successful import, **Then** the dashboard makes clear that the new content lives in `raw/` and is **not** searchable until the next ingest.

---

### User Story 3 — See vault status at a glance (Priority: P2)

When the owner opens the dashboard, a compact status strip shows the current shape of the vault — how many wiki articles exist, how much raw content is waiting to be ingested, and when the wiki was last ingested — derived entirely by reading the filesystem, so it loads instantly and costs nothing.

**Why this priority**: It orients the owner and makes the import → ingest sequencing tangible (pending items visibly accumulate until the next ingest). It is informational rather than essential to the core capture/query loop, so it sits alongside content import at P2 and must not gate the hero.

**Independent Test**: Open the dashboard against the existing vault and confirm the strip shows accurate counts and a last-ingest time without invoking any skill or model call.

**Acceptance Scenarios**:

1. **Given** the dashboard is open, **When** the home view loads, **Then** a status strip displays the wiki article count, the number of raw items awaiting ingest, and the time of the last ingest.
2. **Given** new content has just been imported, **When** the owner returns to the home view, **Then** the pending/raw count reflects the newly added item(s).
3. **Given** an ingest has just completed, **When** the home view reloads, **Then** the last-ingest time and wiki article count update accordingly.
4. **Given** a freshly set-up vault with no content, **When** the home view loads, **Then** the strip shows empty/zeroed counts cleanly rather than erroring.

---

### User Story 4 — Refresh and check the wiki (Priority: P3)

The owner triggers an ingest to fold pending `raw/` content into the wiki, and runs a lint to check wiki quality, viewing the resulting report in the dashboard.

**Why this priority**: Maintenance closes the loop but is run less often than query or import, and depends on content already being present.

**Independent Test**: With pending files in `raw/`, trigger ingest from the dashboard and confirm `wiki/` and `INDEX.md` update; trigger lint and confirm a report is displayed.

**Acceptance Scenarios**:

1. **Given** pending content in `raw/`, **When** the owner triggers ingest, **Then** the wiki updates and a summary of what changed (new/updated articles, INDEX refreshed) is shown.
2. **Given** an existing wiki, **When** the owner triggers lint, **Then** the lint report is displayed in the page.
3. **Given** a long-running ingest, **When** it is in progress, **Then** the UI indicates progress and does not appear frozen.

---

### Edge Cases

- Querying before any content has been ingested (empty or non-existent wiki) returns a clear "no wiki content yet" answer rather than an error.
- A non-PDF file selected for PDF import is rejected with a specific message; a very large PDF either succeeds within the operation budget or fails clearly when it exceeds it.
- A Craft folder/document name that does not exist, or Craft MCP being unavailable, surfaces a specific, actionable error.
- An operation exceeding its expected time budget produces a timeout error rather than an indefinite spinner.
- Two operations triggered close together — the dashboard serializes long-running operations: while one is running, controls for other long-running operations are disabled (or queued and clearly indicated as such) so the owner cannot accidentally launch overlapping work.
- The status strip's source data is missing or malformed (e.g. no `.ingest-manifest.json` yet, or unreadable) — the strip degrades gracefully (falls back to file timestamps, or omits the affected metric) rather than breaking the page.

## Requirements *(mandatory)*

### Functional Requirements

**Query (hero)**

- **FR-001**: The home view MUST present a single, prominent natural-language query input as its primary element.
- **FR-002**: Submitting a query MUST run the existing query capability against the wiki and display the answer inline as rendered Markdown.
- **FR-003**: The system MUST show a visible in-progress state while a query runs and MUST surface a clear error on failure or timeout.
- **FR-004**: Query answers MUST be displayed in the page, and the dashboard MUST also surface a link to the saved `outputs/YYYY-MM-DD_query-*.md` file the query skill produces, so the owner can open or share it.

**Content import**

- **FR-005**: The dashboard MUST provide a paste-Markdown control that adds the pasted text as a new file in `raw/` and confirms the created filename.
- **FR-006**: The dashboard MUST provide a PDF import control that lets the owner select a PDF from anywhere on the local machine (standard file picker) and imports it into `raw/pdf/`.
- **FR-007**: The dashboard MUST provide a Craft import control that accepts a folder and document name and imports the document into `raw/craft/`.
- **FR-008**: Every import MUST report either success (naming the resulting location/file) or a clear, specific failure reason.
- **FR-009**: The dashboard MUST make clear that imported content is staged in `raw/` and becomes searchable only after the next ingest.

**Wiki maintenance**

- **FR-010**: The dashboard MUST provide a control to ingest `raw/` into `wiki/`, displaying a summary of changes on completion.
- **FR-011**: The dashboard MUST provide a control to lint the wiki and display the resulting report.
- **FR-012**: Long-running operations MUST indicate progress and MUST NOT leave the UI in an indeterminate frozen state.
- **FR-013**: The dashboard MUST serialize long-running operations (query, import, ingest, lint): while one is in progress, the controls for other long-running operations MUST be disabled or visibly queued so the owner cannot launch overlapping work.

**Interface & integrity**

- **FR-014**: The owner MUST be able to perform every operation above through a locally-opened web page, with no terminal commands required.
- **FR-015**: All operations MUST act on the single existing vault; the dashboard MUST NOT introduce a second source of truth or duplicate KB logic that lives in the skills.
- **FR-016**: The home view MUST be visually attractive and clearly organized — query front-and-centre, with import and maintenance presented as secondary commands.
- **FR-017**: The dashboard MUST run locally for a single user and MUST NOT expose the KB on a public network or require sign-in.

**Status (at a glance)**

- **FR-018**: The home view MUST display a compact status strip derived solely by reading the vault filesystem and the existing `.ingest-manifest.json` — no skill invocation, no model call, and no new datastore.
- **FR-019**: The status strip MUST show at minimum the total wiki article count, the number of raw items awaiting ingest, and the last ingest time. It MAY also show raw counts broken down by source (paste / PDF / Craft) and counts of saved query and lint outputs, where these are obtainable by simple file inspection.
- **FR-020**: The status strip MUST be read-only — it derives a view and MUST NOT write to or modify any vault state.
- **FR-021**: The status strip MUST exclude any metric requiring content analysis or interpretation (e.g. topic distribution, orphaned or under-linked articles, wiki "health"); such metrics remain out of scope and are surfaced, if at all, only via the existing lint report.

### Key Entities

- **Query Result** — the answer to a question, rendered in the dashboard and persisted by the query skill as an `outputs/` file; the dashboard links to that file.
- **Raw Source File** — a captured item in `raw/` (pasted Markdown, PDF-derived, or Craft-derived) awaiting ingest.
- **Wiki Article / INDEX** — AI-organized knowledge produced by ingest; never edited by the owner.
- **Lint Report** — wiki-quality findings persisted in `outputs/` and shown in the dashboard.
- **Vault Status** — a read-only, computed snapshot of filesystem-derived counts and timestamps; presented in the dashboard but never persisted as new state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The owner can obtain an answer to a KB question entirely from the dashboard, without using a terminal.
- **SC-002**: All six operations — query, paste-Markdown add, PDF import, Craft import, ingest, lint — are invocable from the dashboard.
- **SC-003**: A complete capture → ingest → query loop can be performed from the dashboard alone.
- **SC-004**: Every operation ends in an unambiguous success or failure state visible to the owner; there are no silent or indefinitely hanging operations.
- **SC-005**: Query answers are presented in readable, formatted form rather than raw Markdown source.
- **SC-006**: The dashboard operates within the existing system's performance envelope (query answered in well under a minute for a typical question; a batch ingest of ≤20 files within roughly two minutes).
- **SC-007**: Opening the dashboard shows accurate vault counts and a last-ingest time effectively instantly (status strip renders in under one second on the home view), without invoking any skill or model call.

## Out of Scope

**Planned next (flagged, not built in this feature):**

- **Browsing the wiki as a navigable website** — article pages, wiki-link/backlink navigation, and in-wiki search. This is explicitly coming as a follow-on feature; it is named here so the dashboard's layout can leave a natural place for it, but none of it is built in this iteration.

**Not planned for this feature:**

- Editing wiki articles from the UI (the wiki remains AI-managed and is never user-edited).
- Editing or deleting `raw/` files from the UI (`raw/` is append-only per the base project's constraint).
- Automatic ingest on import — ingest remains an explicit, owner-triggered action.
- Semantic or interpretive status metrics (topic distribution, orphaned or under-linked detection, wiki "health" scoring) — these require content analysis and are surfaced, if at all, only through the existing lint report.
- Multi-user access, remote/hosted deployment, authentication, and mobile-optimized layout.

## Assumptions

- The existing skills (`second-brain-setup`, `-ingest`, `-craft-import`, `-pdf-import`, `-query`, `-lint`, `-md-add`) are the system of record and function as specified. This feature is a front-end over them and introduces no new knowledge-base logic.
- Single owner operating on a local macOS machine, against the vault at `/Users/piero.sierra/Development/SecondBrain/`.
- Craft MCP is configured and available, consistent with the base project's assumption.
- Because a purely static page cannot run the skills directly, a minimal local helper that bridges the browser to Claude Code (skill invocation) is expected. This preserves the base project's "no external services / no hosted deployment / no sign-in" posture at the user-experience level; the specific mechanism is deferred to the implementation plan and is not part of this specification.
- PDF import uses the operating system's standard file picker; the owner may select a PDF from anywhere on the local machine. No designated drop folder is required.
- Long-running operations (query, import, ingest, lint) are serialized in the UI: only one runs at a time; other controls are disabled or visibly queued while one is in progress. This keeps the single-user local model simple and avoids race conditions on the vault.
- Query results, lint reports, and ingest summaries are persisted by the existing skills into `outputs/` and `wiki/`; the dashboard re-uses those artifacts and does not introduce a separate store.
