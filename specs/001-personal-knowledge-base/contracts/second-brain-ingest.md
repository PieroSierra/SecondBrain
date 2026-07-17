# Skill Contract: second-brain-ingest

**Skill**: `/second-brain-ingest`  
**Purpose**: Read new or changed files in `raw/`, synthesise content into `wiki/`, update `INDEX.md`, and update the ingest manifest.

## Invocation

```
/second-brain-ingest
```

No arguments.

## Preconditions

- `CLAUDE.md` must exist (run `/second-brain-setup` first)
- `raw/` directory must exist

## Behaviour

1. Run the deterministic ingestion-state preparation step (or consume the bridge-supplied scan plan)
2. Classify every raw source from its manifest fingerprint: matching metadata is current; changed metadata is confirmed with SHA-256; absent or invalid entries are pending
3. Silently add fingerprints to valid legacy entries; never baseline unmanifested files
4. For each new/changed file:
   a. Read the file content
   b. Identify the topic(s) it covers, guided by the user's declared interests in `CLAUDE.md`
   c. For each topic: read existing `wiki/<topic>.md` if it exists, then create or update it
   d. Ensure cross-links (`[[wikilinks]]`) are added to related articles
5. Rebuild `wiki/INDEX.md` from all current wiki articles
6. After all wiki writes succeed, revalidate processed fingerprints and atomically finalize the manifest through the deterministic helper
7. Report: files processed, articles created, articles updated, files skipped

## Outputs

| Output | Description |
|--------|-------------|
| `wiki/<topic>.md` | Created or updated for each topic identified |
| `wiki/INDEX.md` | Rebuilt with current topic list |
| `raw/.ingest-manifest.json` | Updated with new ingestion timestamps |

## Invariants

- `raw/` files are NEVER modified
- Wiki articles are never deleted by ingest — only created or updated
- Only the deterministic finalizer writes the manifest, after all wiki updates complete successfully

## Error Conditions

| Condition | Behaviour |
|-----------|-----------|
| Non-text file in `raw/` | Skip with logged warning; do not add to manifest |
| Wiki write fails mid-batch | Report partial progress; do not finalize the scan, leaving affected files pending |
| No new or changed files | Report "nothing to ingest" and exit cleanly |
