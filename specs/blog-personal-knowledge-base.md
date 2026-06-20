# How I Built a Personal Knowledge Base as an Engineering Leader

*June 2026 — Leo Liu*

---

## The Problem: Knowledge that Disappears

As an engineering director, I spend a large chunk of my week in meetings, reading articles, consuming research, and processing decisions. By Friday I can rarely recall the nuance of a Monday discussion. By next quarter, the context that drove an important architectural call has evaporated entirely.

The standard responses — better note-taking apps, more rigorous Confluence pages, colour-coded Notion databases — all have the same failure mode: they require manual maintenance. The moment work gets busy, the system rots.

I wanted something different. Something that captures fast, organises itself, and gets more useful over time — not less.

---

## The Architecture: Three Tiers Inspired by Karpathy

Andrej Karpathy described a concept he calls an "idea file" — a place to dump everything interesting without worrying about structure. The structure emerges from an AI layer on top.

Nick Spisak turned that concept into a practical system. I adopted it. The architecture is three tiers.

`raw/` is the brain dump. Unprocessed. You never touch it after writing to it. Everything goes here — articles, meeting notes, research, internal docs, anything.

`wiki/` is the AI-organised knowledge layer. One topic per markdown file, maintained entirely by the AI, cross-linked between related topics. I never write a wiki article by hand.

`outputs/` is where query results land — briefings, comparisons, analyses generated on demand.

**The key rule**: `raw/` is the source of truth. The wiki is always derived from raw content and can always be rebuilt. This means ingestion is zero-friction — you dump first and organise never.

---

## What Goes Into `raw/`

Anything that seems relevant:

**Meeting notes** — I sync Zoom AI-generated summaries automatically via a small Python script using the Zoom OAuth API. Every meeting lands in `raw/meetings/` as markdown within minutes of it ending.

**Articles and research** — the Obsidian Web Clipper Chrome extension sends any webpage to `raw/` with one click.

**Confluence pages** — fetched via the Atlassian MCP integration, which outputs clean markdown directly into the vault.

**Videos** — agent-browser can extract title, description, chapters, and key points from YouTube pages.

The bar for inclusion is low. If I think I might want it later, it goes in. The AI decides what is significant when it compiles the wiki.

---

## What Lives in `wiki/`

After adding raw content, I run the ingest command and the AI reads all new raw sources, creates or updates the relevant topic files, cross-links related topics using `[[topic-name]]` wikilinks, and maintains a master index.

Right now my wiki has articles spanning AI leadership patterns, org restructuring decisions, culture interview frameworks, cloud cost initiatives, travel AI research, people management cycles, and more — all synthesised from meeting notes, articles, and internal docs I consumed over the last few months.

The value compounds. Day one the wiki is sparse. After 30–60 days of consistent input it becomes a genuine knowledge asset that reflects your specific context, not generic internet wisdom.

---

## Querying It

The point is not to store things — it is to *use* them. I query the system by asking Claude questions:

> "What are the main arguments for and against the agent ownership model?"  
> "Summarise the key decisions from the last two weeks of eng-lead meetings."  
> "What do I know about when travellers accept vs. resist AI recommendations?"

Claude reads the wiki and returns a synthesised answer with pointers to sources. This is faster than searching files and more trustworthy than asking a general-purpose AI that doesn't know my context.

---

## How I Actually Built It

I want to be specific here, because most "second brain" posts stay abstract. Here is exactly what I set up.

**The folder structure** is three directories: `raw/` for everything unprocessed, `wiki/` for AI-organised knowledge, and `outputs/` for generated reports and analyses. The schema lives in a `CLAUDE.md` file at the root of the vault — it tells Claude what the system is, how it's organised, and what my interests are. This is what allows Claude to maintain the wiki consistently across sessions without me explaining the rules every time.

**Zoom meeting sync** is the highest-leverage automation. I wrote a Python script (`zoom_web_sync.py`) that authenticates to Zoom via browser session, pulls AI-generated meeting notes from `hub.zoom.us`, and saves them as markdown files to `raw/meetings/` — named with the date and meeting title, e.g. `2026-06-03_core-funnel-eng-leads.md`. The script runs with `--days 7` to catch the past week. A minimum content threshold filters out empty or near-empty notes so low-signal files don't pollute the corpus. After the first SSO login, subsequent runs are fully automatic.

**The ingest CLI** (`ingest.py`) is a unified entry point for all sources. It supports `zoom`, `confluence`, `slack`, and `web` as subcommands. Running `python3 ingest.py zoom --days 7` fetches the past week of meeting notes. Running it with `all` sweeps every source in one command. I run this after any significant batch of new raw content before querying the system.

**Confluence and internal docs** come through the Atlassian MCP integration. I paste or reference a Confluence URL and Claude fetches the page as clean markdown, ready to drop into `raw/`. No copy-pasting, no formatting loss.

**Web articles** go through the Obsidian Web Clipper Chrome extension — one click from any page to `raw/`.

**Slack** is handled by a dedicated Claude skill that scans my key channels and DMs from the past 7 days and produces a structured briefing with three sections: things I should know, key topics being discussed, and actions I should take. It runs every Monday morning.

---

## Making It Self-Evolving

The system doesn't just store things — it maintains itself. This is the part that took the most deliberate design.

**The CLAUDE.md schema is the engine.** Every time Claude opens this vault, it reads the schema and knows the rules: never modify `raw/`, maintain one file per topic in `wiki/`, start every wiki article with a summary paragraph, cross-link with `[[topic-name]]` wikilinks, keep `INDEX.md` current. Because these rules are in a file rather than in my head, the behaviour is consistent across every session and every AI tool.

**Wiki compilation is incremental and additive.** When I run ingest after adding new raw sources, Claude reads what's new, updates the relevant wiki articles, creates new ones if a topic doesn't exist yet, and adds cross-links. Nothing is overwritten wholesale — the wiki accumulates. Older decisions and context stay in place. New raw sources refine or expand them.

**The lint command surfaces gaps.** Monthly I run a lint pass: Claude reads the entire wiki, flags contradictions between articles, identifies claims that aren't backed by any raw source, and suggests what content I should add next. This is how the system tells me what it doesn't know yet. It turns knowledge gaps from invisible to actionable.

**Interests guide synthesis.** My `CLAUDE.md` declares explicit interests: engineering leadership, people management, product strategy, AI skills, cross-team communications. When Claude compiles the wiki, it uses these to decide what deserves its own article vs. what is a footnote in an existing one. The same raw content — say, a meeting about org restructuring — gets synthesised differently for someone whose interests are finance vs. someone whose interests are team design and leadership.

**The outputs layer closes the loop.** Queries don't just retrieve — they generate. When I ask "what do I know about when to use agent ownership vs. a strike team?", the result lands in `outputs/` as a dated markdown file. Over time, `outputs/` becomes its own record of how my thinking evolved. I can look back at an analysis from two months ago and see what I knew then vs. now.

The result is a system that gets more useful every week without requiring more maintenance every week. The marginal cost of adding new content is near zero. The marginal value of each new piece is additive to everything already there.

---

## The Tools

The storage layer is **Obsidian** — local, markdown-native, free, and privacy-preserving. Web capture goes through the **Obsidian Web Clipper** Chrome extension, which sends any page to `raw/` in one click. The AI layer is **Claude Code** running the second-brain skill, which drives ingest, compile, lint, and query commands. Meeting sync runs through a custom **Zoom browser-session script** that auto-fetches AI-generated meeting summaries from Zoom Hub and writes them directly to `raw/meetings/`. Authenticated internal pages come through the **Atlassian MCP** integration, which fetches Confluence and Jira content as clean markdown. The **ingest CLI** unifies all sources — Zoom, Slack, Confluence, and web — into a single command.

The second-brain skill is free and installable via a guided setup wizard. It works across Claude Code, Codex, Gemini CLI, and OpenCode — so you are not locked into one AI tool.

---

## What I've Learned After Using It

**Start narrow.** Pick one input stream — meeting notes is the highest ROI for leaders — and automate it before adding anything else.

**Don't curate `raw/`.** The temptation is to only save "important" things. Resist it. The AI decides what matters. Your job is to lower the activation energy of capture to near zero.

**Lint monthly.** The system has a lint command that flags contradictions, gaps, and unsupported claims in the wiki. Running it occasionally keeps the knowledge clean and surfaces what raw content you should add next.

**The moat is real.** After a few months, your wiki reflects *your* context — your team, your product domain, your ongoing decisions. No general AI has this. It becomes a genuine competitive advantage for the speed and quality of your thinking.

---

## Getting Started

1. Install Obsidian and create a vault
2. Install the second-brain skill (free, link below)
3. Set up the folder structure: `raw/`, `wiki/`, `outputs/`
4. Pick one input stream and automate it
5. Run ingest after adding your first batch of content
6. Ask a question

The setup takes an afternoon. The returns start within days and compound for months.

---

*The system described here builds on Andrej Karpathy's "idea file" concept and Nick Spisak's one-click Claude skill implementation.*
