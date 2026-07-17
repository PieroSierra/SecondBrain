# Feature Specification: Personal Knowledge Base (Second Brain)

**Feature Branch**: `001-personal-knowledge-base`  
**Created**: 2026-06-16  
**Status**: Draft  
**Input**: User description: "I want to build a 'second brain' in this folder, a Personal Knowledge Base where I can dump content and it ingests it, organizes it, and lets me query or browse it. I also want to be able to import notes from my Craft macOS application (which is available via MCP) so I'll want a skill for that."

## Clarifications

### Session 2026-06-16

- Q: How should PDF ingestion be handled — auto-detected inline during standard ingest, or via a dedicated PDF import skill that converts to markdown first? → A: Dedicated PDF import skill that converts PDFs to markdown and writes them into `raw/pdf/`; standard ingest then processes them as normal markdown
- Q: When the Craft import skill runs, what scope of notes should it retrieve — all notes, a configured space/folder, or only explicitly tagged notes? → A: A specific Craft space or folder (configured in `CLAUDE.md`), or a specific individual note targeted at invocation time
- Q: How does the ingest command track which files have already been processed — manifest file, full rebuild, or git history? → A: A manifest file (`raw/.ingest-manifest.json`) records each ingested file's path, timestamps, size, and SHA-256 fingerprint; metadata changes trigger a hash comparison to find genuine content changes
- Q: How does the user declare their interests in `CLAUDE.md` for the first time — guided setup skill or manual template editing? → A: A one-time setup skill walks the user through declaring their interests interactively and writes the `CLAUDE.md` file
- Q: What naming convention should output files in `outputs/` follow? → A: `YYYY-MM-DD_query-<slug>.md` for query outputs and `YYYY-MM-DD_lint.md` for lint reports (date + type prefix + brief slug)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ingest and Organise Raw Content (Priority: P1)

The user drops unstructured content into a `raw/` folder — articles saved from the web, pasted notes, exported documents — and runs an ingest command. The system reads the new raw content, updates or creates topic articles in a `wiki/` folder with cross-links to related topics, and keeps a master index current. The user never needs to manually organise anything.

**Why this priority**: This is the core value proposition of the system. Without it nothing else works. All other stories depend on content existing in the wiki.

**Independent Test**: Can be tested by adding a markdown file to `raw/`, running the ingest command, and verifying that a corresponding or updated topic file appears in `wiki/` with accurate content and cross-links.

**Acceptance Scenarios**:

1. **Given** one or more new markdown files exist in `raw/` that have not been ingested before, **When** the user runs the ingest command, **Then** the system creates or updates relevant topic files in `wiki/`, adds `[[topic-name]]` cross-links to related topics, and updates `INDEX.md`
2. **Given** a topic file already exists in `wiki/` and new raw content touches that topic, **When** the user runs ingest, **Then** the existing wiki article is expanded or refined without losing previously synthesised content
3. **Given** raw content is added but ingest has not been run, **When** the user checks `wiki/`, **Then** the new content is not yet reflected (ingest is explicit, not automatic)
4. **Given** the ingest command is run with no new raw content, **When** the command completes, **Then** the system reports that nothing has changed and no wiki files are modified

---

### User Story 2 - Import Notes from Craft (Priority: P2)

The user invokes a dedicated Claude Code skill that connects to their Craft macOS application via the Craft MCP integration, and retrieves notes from either (a) a specific Craft space or folder configured in `CLAUDE.md`, or (b) a specific individual note identified at invocation time. The retrieved notes are written into `raw/craft/` as clean markdown files. The user can then run the standard ingest command to incorporate those notes into the wiki.

**Why this priority**: Craft is the user's primary note-taking application. Bridging it into the PKB without manual copy-paste is a high-leverage automation that removes the main capture friction point.

**Independent Test**: Can be tested independently by invoking the Craft import skill against a configured space or a named note, and verifying that markdown files appear in `raw/craft/` with correct content and filename conventions, without needing to run ingest.

**Acceptance Scenarios**:

1. **Given** a Craft space or folder is configured in `CLAUDE.md` and the MCP integration is available, **When** the user invokes the Craft import skill without arguments, **Then** all notes in that configured space/folder are retrieved and saved as markdown files under `raw/craft/` with date-prefixed filenames (e.g., `2026-06-16_note-title.md`)
2. **Given** the user invokes the Craft import skill with a specific note identifier, **When** the skill runs, **Then** only that individual note is retrieved and saved to `raw/craft/`, regardless of the configured space/folder
3. **Given** a Craft note has already been imported previously, **When** the user imports again, **Then** the existing file is updated if the note content has changed, and no duplicate files are created
4. **Given** the Craft MCP integration is unavailable or returns an error, **When** the user invokes the skill, **Then** the skill surfaces a clear error message and no partial files are written to `raw/`
5. **Given** the import completes successfully, **When** the user runs the ingest command, **Then** the Craft notes are processed identically to any other raw content

---

### User Story 3 - Query the Knowledge Base (Priority: P3)

The user asks a natural-language question about something they have previously captured. The system reads the wiki and returns a synthesised answer with pointers back to the source files that informed it. The answer lands in `outputs/` as a dated markdown file so the user can review their thinking over time.

**Why this priority**: Retrieval is the payoff of the whole system, but it requires P1 (populated wiki) to deliver value. It is independently testable once content exists.

**Independent Test**: Can be tested by asking a question whose answer is covered by existing wiki content, then verifying that a coherent response file is written to `outputs/` and references the relevant wiki articles.

**Acceptance Scenarios**:

1. **Given** the wiki contains relevant content, **When** the user asks a natural-language question, **Then** the system returns a synthesised answer that references specific wiki articles as sources
2. **Given** the wiki does not contain content relevant to the question, **When** the user asks the question, **Then** the system acknowledges the gap rather than fabricating an answer, and suggests what raw content might fill it
3. **Given** a query is answered, **When** the response is generated, **Then** a dated file is saved to `outputs/` containing the question and the full answer

---

### User Story 4 - Import PDFs into the Knowledge Base (Priority: P3)

The user invokes a dedicated PDF import skill, pointing it at one or more PDF files. The skill extracts the text content, converts it to clean markdown, and writes the result into `raw/pdf/` with a date-prefixed filename. The user can then run the standard ingest command to incorporate that content into the wiki.

**Why this priority**: PDFs are a common document format (research papers, exported reports, shared briefs). Supporting them as a first-class import path closes a significant capture gap alongside Craft.

**Independent Test**: Can be tested independently by invoking the PDF import skill against a local PDF and verifying that a markdown file appears in `raw/pdf/` with coherent extracted text, without needing to run ingest.

**Acceptance Scenarios**:

1. **Given** a valid PDF file is provided to the PDF import skill, **When** the skill runs, **Then** the extracted text is saved as a markdown file under `raw/pdf/` with a date-prefixed filename (e.g., `2026-06-16_document-title.md`)
2. **Given** a PDF that has already been imported, **When** the skill is run again for the same file, **Then** the existing markdown file is updated if the source PDF differs, and no duplicate file is created
3. **Given** a PDF that cannot be read (corrupted, password-protected, or empty), **When** the skill attempts import, **Then** a clear error is reported and no partial file is written to `raw/`
4. **Given** a successfully imported PDF, **When** the user runs the ingest command, **Then** the PDF-derived markdown is processed identically to any other raw content

---

### User Story 5 - Lint the Knowledge Base (Priority: P4)

The user runs a lint command periodically. The system reads the entire wiki, identifies contradictions between articles, flags claims not backed by any raw source, and suggests what content the user should add next. The lint report is written to `outputs/`.

**Why this priority**: Maintenance and quality of the knowledge base matter more as the corpus grows, but the system is fully useful before this feature is added.

**Independent Test**: Can be tested once a meaningful wiki exists by running the lint command and verifying the output report identifies at least one actionable finding (gap, contradiction, or unsupported claim).

**Acceptance Scenarios**:

1. **Given** the wiki contains multiple articles, **When** the user runs the lint command, **Then** the system produces a report listing contradictions, unsupported claims, and suggested content additions
2. **Given** the wiki is consistent and all claims are traceable to raw sources, **When** lint runs, **Then** the report confirms a clean state rather than forcing false positives

---

### Edge Cases

- What happens when an image file is placed directly in `raw/`? The system should skip it gracefully during ingest and log a warning (images are not supported as direct raw input).
- What happens when a PDF import fails mid-way (e.g., partial extraction)? No partial file should be written; the error should be surfaced clearly so the user can retry.
- What happens when a Craft note has no title? A fallback filename should be generated using the note's creation date and a truncated content preview.
- What happens when the wiki becomes very large (hundreds of articles) and ingest must process many updates at once? The system should process incrementally without losing partially completed work.
- What happens when two raw sources make contradictory claims about the same topic? Ingest should preserve both perspectives in the wiki article and note the conflict rather than silently discarding one.
- What happens when `raw/` contains a file that has already been ingested and hasn't changed? The system should skip it without modifying the wiki.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST maintain a three-tier folder structure: `raw/` for unprocessed input, `wiki/` for AI-organised knowledge, and `outputs/` for generated results
- **FR-002**: The system MUST provide an ingest command that reads new or content-changed files in `raw/` and updates `wiki/` accordingly; ingestion state MUST be tracked in `raw/.ingest-manifest.json` with each file's path, timestamps, size, and SHA-256 fingerprint
- **FR-003**: The ingest command MUST create new wiki topic files when new subjects are encountered, and update existing ones when new raw content touches an existing topic
- **FR-004**: Wiki articles MUST be cross-linked using `[[topic-name]]` wikilink syntax wherever related topics are referenced
- **FR-005**: The system MUST maintain a `wiki/INDEX.md` file that lists all current wiki topics with one-line summaries, updated on every ingest
- **FR-006**: The ingest operation MUST NOT modify source files in `raw/`; users and importers MAY update an existing source path, and ingestion MUST detect changed bytes
- **FR-007**: The system MUST provide a query capability that accepts a natural-language question and returns a synthesised answer drawn from `wiki/` content
- **FR-008**: Query responses MUST cite the specific wiki articles that informed the answer
- **FR-009**: Every query response MUST be saved to `outputs/` using the filename convention `YYYY-MM-DD_query-<slug>.md`, where the slug is a short kebab-case summary of the question; the file MUST contain the original question and the full answer
- **FR-010**: The system MUST provide a lint command that scans `wiki/` for contradictions between articles, claims unsupported by any raw source, and topic gaps
- **FR-011**: Lint reports MUST be saved to `outputs/` using the filename convention `YYYY-MM-DD_lint.md`
- **FR-012**: The system MUST provide a Craft import skill that connects to the Craft macOS application via the Craft MCP integration and retrieves notes for placement in `raw/craft/`
- **FR-012a**: The Craft import skill MUST support two targeting modes: (1) bulk import of all notes from a Craft space or folder configured in `CLAUDE.md`, and (2) single-note import identified by a note name or identifier passed at invocation time
- **FR-013**: Imported Craft notes MUST be saved as markdown files with date-prefixed filenames (e.g., `YYYY-MM-DD_note-title.md`)
- **FR-014**: The Craft import skill MUST detect previously imported notes and update rather than duplicate them
- **FR-015**: The Craft import skill MUST report a clear error and write no partial files if the Craft MCP integration is unavailable
- **FR-016**: The system MUST be driven through Claude Code skills, requiring no separate runtime dependencies beyond what Claude Code already provides
- **FR-017**: A `CLAUDE.md` file at the vault root MUST define the system schema, folder rules, and the user's declared interests, so the AI behaves consistently across sessions without re-explanation
- **FR-017a**: The system MUST provide a one-time setup skill that interactively guides the user through declaring their interests and generates the `CLAUDE.md` file; the skill MUST be idempotent so it can update an existing `CLAUDE.md` without losing manually added content
- **FR-018**: The system MUST provide a PDF import skill that extracts text from a PDF file, converts it to markdown, and writes the result into `raw/pdf/` with a date-prefixed filename
- **FR-019**: The PDF import skill MUST detect previously imported PDFs (by filename or source identity) and update rather than duplicate them
- **FR-020**: The PDF import skill MUST report a clear error and write no partial files if the PDF is unreadable, password-protected, or empty
- **FR-021**: Markdown files produced by the PDF import skill MUST be processed by the standard ingest command without any special handling

### Key Entities

- **Raw Source**: An unprocessed file in `raw/` representing a single unit of captured content (a note, article, meeting summary, or imported document). Attributes: filename, creation date, source type (manual, craft-import, web-clip, etc.), ingestion status.
- **Wiki Article**: A synthesised, AI-maintained markdown file in `wiki/` covering a single topic. Attributes: topic name, summary paragraph, body content, cross-links to related topics, list of raw sources it was derived from.
- **Query Output**: A markdown file in `outputs/` named `YYYY-MM-DD_query-<slug>.md`, containing the original question and the system's synthesised answer with source citations.
- **Lint Report**: A markdown file in `outputs/` named `YYYY-MM-DD_lint.md`, listing contradictions, unsupported claims, and content gap recommendations found during a lint pass.
- **Index**: `wiki/INDEX.md` — a continuously maintained list of all wiki topics with one-line summaries and links.
- **Ingest Manifest**: `raw/.ingest-manifest.json` — a machine-maintained record of every file that has been ingested, keyed by file path with timestamps and a content fingerprint. Never modified by the user; updated atomically by deterministic ingestion-state code after each successful run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The user can drop content into `raw/` and run a single command to have it reflected in organised wiki articles, with the full cycle completing in under 2 minutes for a batch of up to 20 files
- **SC-002**: The user can ask a natural-language question and receive a sourced answer in under 60 seconds
- **SC-003**: After 30 days of regular use, at least 80% of queries return answers traceable to specific raw sources rather than requiring the user to search files manually
- **SC-004**: The Craft import skill completes successfully for any Craft note that is accessible via the MCP integration, with zero data loss
- **SC-005**: A new user can run the setup skill, complete the interests declaration, and successfully complete their first ingest within a single session, guided entirely by the setup skill and the resulting `CLAUDE.md` file
- **SC-006**: The lint command identifies at least one actionable finding (gap, contradiction, or unsupported claim) per 50 wiki articles in a mature knowledge base

## Assumptions

- The user is the sole author and consumer of this knowledge base — multi-user access, permissions, and sharing are out of scope
- The Craft MCP integration is already configured and available in the user's Claude Code environment
- PDF files are supported via a dedicated PDF import skill that converts them to markdown before ingest; image files remain out of scope for v1
- The user will run ingest manually after adding raw content; real-time or automatic background ingestion is out of scope
- The system relies entirely on Claude Code skills for AI processing; no external AI services, databases, or servers are required
- The folder structure lives in the current working directory (`/Users/piero.sierra/Development/SecondBrain`) and is not synced to a cloud storage service by this system (the user may sync independently via their own tools)
- Web clipping (browser extension) and meeting sync automation are out of scope for the initial version; the Craft import skill is the primary automated ingestion path
- The user's declared interests (to be specified in `CLAUDE.md`) will guide how the AI prioritises and synthesises wiki content
