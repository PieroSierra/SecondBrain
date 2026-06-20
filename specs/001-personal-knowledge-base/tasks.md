# Tasks: Personal Knowledge Base (Second Brain)

**Input**: Design documents from `specs/001-personal-knowledge-base/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each skill.

**Implementation note**: This is a Claude Code skills-only project. All "source code" is Claude Code skill instruction files (`.claude/skills/<name>/prompt.md`). There are no compiled artifacts, test frameworks, or runtime dependencies.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)

---

## Phase 1: Setup (Skill Directory Structure)

**Purpose**: Create the directory scaffold for all six skills before any skill content is written.

- [x] T001 [P] Create `.claude/skills/second-brain-setup/` directory with empty `prompt.md` stub
- [x] T002 [P] Create `.claude/skills/second-brain-ingest/` directory with empty `prompt.md` stub
- [x] T003 [P] Create `.claude/skills/second-brain-craft-import/` directory with empty `prompt.md` stub
- [x] T004 [P] Create `.claude/skills/second-brain-pdf-import/` directory with empty `prompt.md` stub
- [x] T005 [P] Create `.claude/skills/second-brain-query/` directory with empty `prompt.md` stub
- [x] T006 [P] Create `.claude/skills/second-brain-lint/` directory with empty `prompt.md` stub

**Checkpoint**: All six skill directories exist — skill authoring can begin in any order.

---

## Phase 2: Foundational (Vault Setup Skill)

**Purpose**: The setup skill is the entry point for the entire system — it creates `raw/`, `wiki/`, `outputs/`, and generates `CLAUDE.md`. All other skills depend on a correctly initialised vault.

**⚠️ CRITICAL**: The ingest and Craft import skills read configuration from `CLAUDE.md`. Define its schema here before writing those skills.

- [x] T007 Define `CLAUDE.md` vault schema — document the exact sections, headings, and configuration block formats (`[CRAFT]`, `[INTERESTS]`) that the setup skill will generate; record in `specs/001-personal-knowledge-base/contracts/second-brain-setup.md` as a `CLAUDE.md Output Format` section
- [x] T008 Write the `second-brain-setup` skill in `.claude/skills/second-brain-setup/prompt.md` — implement the full interactive flow per `contracts/second-brain-setup.md`: ask for interests, ask for Craft space/folder, create `raw/` / `wiki/` / `outputs/` directories if absent, write or update `CLAUDE.md` with vault schema + declared interests + Craft config; skill must be idempotent (re-run safe)
- [x] T009 Manually verify the setup skill: invoke `/second-brain-setup`, confirm `CLAUDE.md` is created with correct sections, confirm `raw/`, `wiki/`, `outputs/` directories exist

**Checkpoint**: Vault initialised — `CLAUDE.md` exists with interests and Craft config. All subsequent skills can be developed and tested.

---

## Phase 3: User Story 1 — Ingest Raw Content (Priority: P1) 🎯 MVP

**Goal**: User drops markdown files into `raw/`, runs `/second-brain-ingest`, and wiki articles appear in `wiki/` with cross-links and an updated `INDEX.md`.

**Independent Test**: Add 2–3 markdown files to `raw/`, invoke `/second-brain-ingest`, verify `wiki/` contains topic files and `wiki/INDEX.md` is populated. Run again with no changes; verify "nothing to ingest" is reported.

- [x] T010 [US1] Write the `second-brain-ingest` skill in `.claude/skills/second-brain-ingest/prompt.md` — implement per `contracts/second-brain-ingest.md`: read `raw/.ingest-manifest.json`, scan `raw/` recursively, compare `last_modified` vs `ingested_at`, for each new/changed file identify topics (using declared interests from `CLAUDE.md`), create or update `wiki/<topic>.md` with summary paragraph + body + `[[wikilinks]]` + sources footer, rebuild `wiki/INDEX.md`, write updated manifest atomically; skip non-markdown files with a logged warning; never modify files in `raw/`
- [x] T011 [US1] Add ingest manifest format specification inline in `.claude/skills/second-brain-ingest/prompt.md` — embed the exact JSON schema from `data-model.md` (keys: relative path → `{last_modified, ingested_at}`) and the wiki article format (summary paragraph first, sources footer last) so the skill produces consistent output across sessions
- [x] T012 [US1] Manually verify the ingest skill end-to-end: create 3 fixture markdown files in `raw/` covering different topics, invoke `/second-brain-ingest`, confirm wiki articles are created with correct structure, confirm `INDEX.md` lists all topics, confirm manifest records all three files, run ingest again and confirm no changes are made (idempotent)

**Checkpoint**: US1 fully functional — vault ingests raw content and produces a browsable wiki. This is the MVP.

---

## Phase 4: User Story 2 — Craft Import (Priority: P2)

**Goal**: User invokes `/second-brain-craft-import` and Craft notes land in `raw/craft/` as markdown files, ready for ingest.

**Independent Test**: Invoke `/second-brain-craft-import` (bulk mode), verify markdown files appear in `raw/craft/` with correct date-prefixed filenames. Invoke with a specific note name (single-note mode), verify only that file is written. Invoke again; verify no duplicates are created for unchanged notes.

- [x] T013 [US2] Write the `second-brain-craft-import` skill in `.claude/skills/second-brain-craft-import/SKILL.md` — implemented with targeted Folder/DocumentName mode per clarified design; create/update/skip logic; YAML front-matter header; error handling for unavailable MCP
- [x] T014 [P] [US2] Add Craft MCP invocation examples to `.claude/skills/second-brain-craft-import/SKILL.md` — documented craft_get_spaces, craft_get_documents, craft_get_document tool reference with fallback guidance
- [x] T015 [US2] Manually verify the Craft import skill: invoke with a specific Folder/DocumentName and confirm file appears in `raw/craft/`; re-import and confirm no duplicates; verify that running `/second-brain-ingest` after import processes the Craft notes correctly

**Checkpoint**: US1 + US2 functional — Craft notes flow automatically into the knowledge base.

---

## Phase 5: User Story 4 — PDF Import (Priority: P3)

**Goal**: User invokes `/second-brain-pdf-import /path/to/doc.pdf` and extracted markdown appears in `raw/pdf/`, ready for ingest.

**Independent Test**: Run `/second-brain-pdf-import` against a test PDF, verify a markdown file appears in `raw/pdf/` with correct metadata header and extracted content. Run again; verify no duplicate. Run against a password-protected or missing file; verify clean error with no partial output.

- [x] T016 [US4] Write the `second-brain-pdf-import` skill in `.claude/skills/second-brain-pdf-import/prompt.md` — implement per `contracts/second-brain-pdf-import.md`: accept path argument and optional title override; verify file exists and is not empty; read PDF using Claude's native Read tool (paginate in ≤20-page batches for large PDFs and concatenate); determine output filename `raw/pdf/YYYY-MM-DD_<slug>.md`; apply create/update/skip logic; write output with YAML front-matter header (`source`, `imported`, `pages`); report partial extraction warnings if some pages fail; handle errors (not found, password-protected, empty) with no partial writes
- [x] T017 [US4] Manually verify the PDF import skill: import a multi-page PDF and confirm full extraction with metadata header; import a PDF already imported and confirm it is skipped (identical) or updated (changed); attempt import of a non-existent path and confirm clean error; run `/second-brain-ingest` after import and confirm the PDF-derived markdown is processed correctly

**Checkpoint**: US1 + US2 + US4 functional — markdown, Craft, and PDF content all feed into the knowledge base.

---

## Phase 6: User Story 3 — Query the Knowledge Base (Priority: P3)

**Goal**: User asks a natural-language question and receives a sourced answer; the response is saved to `outputs/`.

**Independent Test**: With a populated wiki, invoke `/second-brain-query "What do I know about [topic]?"` — verify a coherent sourced answer is displayed and a file matching `YYYY-MM-DD_query-<slug>.md` appears in `outputs/`. Ask a question on a topic not in the wiki; verify the skill acknowledges the gap rather than fabricating.

- [x] T018 [US3] Write the `second-brain-query` skill in `.claude/skills/second-brain-query/prompt.md` — implement per `contracts/second-brain-query.md`: accept the question as argument; read `wiki/INDEX.md` to identify relevant topics; read relevant wiki article(s); synthesise a grounded answer with `[[wikilink]]` citations; acknowledge gaps explicitly when wiki has no relevant content; generate output filename using slug of first 5–6 words (lowercase, kebab-case, max 40 chars); write query output file to `outputs/YYYY-MM-DD_query-<slug>.md` with question + answer + sources footer; display answer to user and note the output file path; never modify `raw/` or `wiki/`
- [x] T019 [US3] Manually verify the query skill: with a populated wiki from earlier phases, ask a question covered by wiki content and confirm a sourced answer is returned and saved to `outputs/`; ask a question not covered by the wiki and confirm gap acknowledgement; verify output filename follows `YYYY-MM-DD_query-<slug>.md` convention

**Checkpoint**: US1–US4 functional — the knowledge base can now be queried as well as populated.

---

## Phase 7: User Story 5 — Lint the Knowledge Base (Priority: P4)

**Goal**: User runs `/second-brain-lint` and receives a report identifying contradictions, unsupported claims, and content gaps.

**Independent Test**: With a wiki containing at least 5 articles, invoke `/second-brain-lint` — verify a report file appears in `outputs/YYYY-MM-DD_lint.md` with structured sections for contradictions, unsupported claims, and gaps. Verify that a clean wiki produces a report confirming clean state rather than a blank file.

- [x] T020 [US5] Write the `second-brain-lint` skill in `.claude/skills/second-brain-lint/prompt.md` — implement per `contracts/second-brain-lint.md`: read all articles in `wiki/`; for each article check for claims that contradict claims in other articles on the same topic; check whether key claims are traceable to a source listed in the article's sources footer; scan `raw/` filenames and topics to identify subjects not yet in the wiki (gap suggestions); generate lint report with sections: Contradictions, Unsupported Claims, Suggested Content Gaps, Summary; write to `outputs/YYYY-MM-DD_lint.md`; report clean state explicitly when no issues found; never modify `raw/` or `wiki/`
- [x] T021 [US5] Manually verify the lint skill: with a populated wiki, invoke `/second-brain-lint` and confirm the report is written to `outputs/`; verify the report is structured with all required sections; confirm clean-state reporting when wiki is consistent

**Checkpoint**: All five user stories functional — the knowledge base captures, organises, queries, and self-audits.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Consistency, end-to-end validation, and discoverability.

- [x] T022 [P] Review all six skill `prompt.md` files for terminology consistency — confirm all use the same folder names (`raw/`, `wiki/`, `outputs/`), same manifest path (`raw/.ingest-manifest.json`), same filename conventions, and same `CLAUDE.md` configuration block names (`[CRAFT]`, `[INTERESTS]`)
- [x] T023 [P] Add a skills registry comment to each skill's `prompt.md` referencing its contract file in `specs/001-personal-knowledge-base/contracts/` so future maintainers can trace prompt logic back to the spec
- [x] T024 Run end-to-end workflow validation per `specs/001-personal-knowledge-base/quickstart.md`: setup → import (Craft or PDF) → ingest → query → lint; confirm each step produces the expected outputs without errors
- [x] T025 [P] Verify all six skills appear correctly in Claude Code's skill list (invocable via `/second-brain-*`); confirm skill names match their directory names

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — all T001–T006 can start immediately and run in parallel
- **Foundational (Phase 2)**: Depends on Phase 1 (directories must exist); T007 before T008 (schema before skill); T009 after T008
- **User Stories (Phases 3–7)**: All depend on Foundational phase (vault initialised, CLAUDE.md schema known); individual user story phases are otherwise independent of each other and can be worked in priority order or in parallel
- **Polish (Phase 8)**: Depends on all desired user story phases being complete

### User Story Dependencies

- **US1 — Ingest (P1)**: Can start as soon as Foundational is complete; no dependency on other user stories
- **US2 — Craft Import (P2)**: Can start as soon as Foundational is complete; independent of US1 (but Craft files need ingest to reach wiki)
- **US4 — PDF Import (P3)**: Can start as soon as Foundational is complete; independent of US1 and US2
- **US3 — Query (P3)**: Can start as soon as Foundational is complete; benefits from US1 being complete for meaningful testing
- **US5 — Lint (P4)**: Can start as soon as Foundational is complete; benefits from US1 being complete for meaningful testing

### Within Each User Story

- Skill authoring task before verification task
- Inline specification task (if present, e.g., T011, T014) can run in parallel with or before authoring

### Parallel Opportunities

- All six Phase 1 directory creation tasks (T001–T006) run in parallel
- After Foundational completes: US1, US2, US4, US3, US5 can all be authored in parallel (different skill files)
- Within US2: T013 (skill authoring) and T014 (MCP examples) can run in parallel
- Phase 8 polish tasks T022, T023, T025 can run in parallel

---

## Parallel Example: Phase 1

```
# All six skill directories created in one pass:
T001: .claude/skills/second-brain-setup/
T002: .claude/skills/second-brain-ingest/
T003: .claude/skills/second-brain-craft-import/
T004: .claude/skills/second-brain-pdf-import/
T005: .claude/skills/second-brain-query/
T006: .claude/skills/second-brain-lint/
```

## Parallel Example: User Story Authoring (after T009 complete)

```
# All skills can be authored concurrently (different files):
T010+T011: second-brain-ingest/prompt.md
T013+T014: second-brain-craft-import/prompt.md
T016:       second-brain-pdf-import/prompt.md
T018:       second-brain-query/prompt.md
T020:       second-brain-lint/prompt.md
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T006)
2. Complete Phase 2: Foundational (T007–T009) — vault initialised
3. Complete Phase 3: US1 Ingest (T010–T012)
4. **STOP and VALIDATE**: Drop files into `raw/`, run `/second-brain-ingest`, browse `wiki/` — the core value proposition is live
5. Ship MVP

### Incremental Delivery

1. Setup + Foundational → vault ready
2. US1 Ingest → core wiki pipeline live (MVP)
3. US2 Craft Import → primary capture automation live
4. US4 PDF Import → PDF capture live
5. US3 Query → retrieval live
6. US5 Lint → quality maintenance live
7. Polish → system coherent and validated end-to-end

### Single-Developer Sequence (Recommended)

Given this is a personal project with one developer:

1. T001–T006 (parallel — create all dirs in one session)
2. T007 → T008 → T009 (sequential — define schema, write skill, verify)
3. T010 → T011 → T012 (US1 — write and verify ingest)
4. T013 → T014 → T015 (US2 — write and verify Craft import)
5. T016 → T017 (US4 — write and verify PDF import)
6. T018 → T019 (US3 — write and verify query)
7. T020 → T021 (US5 — write and verify lint)
8. T022–T025 (polish)

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to its user story for traceability
- No automated tests — each story has a manual verification task instead
- Each skill's `prompt.md` is the only deliverable for that skill; it should be self-contained and reference the relevant contract file
- Commit after each phase checkpoint
- Stop at any checkpoint to use and validate the system before continuing
- The MVP (US1 complete) delivers usable value on day one
