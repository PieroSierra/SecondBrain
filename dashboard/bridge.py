#!/usr/bin/env python3
"""Second Brain dashboard bridge.

Thin local HTTP proxy from the browser to the `claude` CLI.

Responsibilities:
  - Serve the static dashboard (index.html, styles.css, app.js, lib/*).
  - On POST /run, look up `kind` in PROMPT_TEMPLATES, exec `claude -p ...
    --output-format json`, and forward the JSON to the page.
  - On GET /status (future story), read the filesystem.

Non-responsibilities (deliberate):
  - No KB logic. The bridge never parses the model's `result` text to make
    decisions. The PROMPT_TEMPLATES dict is the only KB-aware code here.
  - No persisted state. No DB, no cache, no log file beyond a single run.
  - No external binary other than `claude`. No shell=True.

Spec: specs/002-interactive-dashboard/plan.md
Contract: specs/002-interactive-dashboard/contracts/bridge-http.md
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

VAULT_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = VAULT_ROOT / "dashboard"
OUTPUTS_DIR = VAULT_ROOT / "outputs"
RAW_DIR = VAULT_ROOT / "raw"

DEFAULT_PORT = 4173


def _load_env_file() -> None:
    """Load .env from vault root into os.environ.

    Uses setdefault so real environment variables always take precedence,
    allowing per-shell overrides without editing this file.
    """
    env_path = VAULT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    except OSError:
        pass


_load_env_file()

# Override via CLAUDE_BIN env var or a .env file at the vault root.
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")

# Which agent CLI backs the skills: "claude" (default) or "codex". Both run the
# SAME skills (one canonical .claude/skills/ tree, exposed to Codex via a
# .agents/skills link — see _ensure_skills_link). Anything unrecognised falls
# back to claude so a typo never silently changes the safety model.
AGENT_ENGINE = os.environ.get("AGENT_ENGINE", "claude").strip().lower()
if AGENT_ENGINE not in ("claude", "codex"):
    AGENT_ENGINE = "claude"

_CRAFT_ENABLED = os.environ.get("CRAFT_ENABLED", "").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Model tier selection
# ---------------------------------------------------------------------------
# Claude accepts tier aliases maintained by Anthropic — no model-ID bookkeeping
# needed; when a new Sonnet ships, "--model sonnet" silently picks it up.
# Update _CODEX_TIER_MAP when new Codex models are released.
_CLAUDE_TIER_MAP: dict[str, str] = {
    "fable":  "fable",
    "opus":   "opus",
    "sonnet": "sonnet",
    "haiku":  "haiku",
}
_CODEX_TIER_MAP: dict[str, str] = {
    "sol":   "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "luna":  "gpt-5.6-luna",
}

# Module-level live state — updated by POST /set-model without bridge restart.
# "default" means: pass no --model flag, use the CLI's own default.
# CPython GIL makes single-assignment atomic; no explicit lock needed here.
_CLAUDE_MODEL_TIER: str = os.environ.get("CLAUDE_MODEL", "default").strip().lower()
_CODEX_MODEL_TIER:  str = os.environ.get("CODEX_MODEL",  "default").strip().lower()

# ---------------------------------------------------------------------------
# Security: CSRF gate
# ---------------------------------------------------------------------------
#
# The bridge binds to 127.0.0.1, but "localhost-only" is NOT a security
# boundary on its own: any web page the user visits can issue a cross-origin
# fetch() to http://127.0.0.1:<port>/run and trigger a skill. Because each
# skill runs the `claude` CLI with file-system access, that is a real RCE
# vector. We close it with two independent checks on every state-changing or
# data-returning endpoint (see DashboardHandler._authorize):
#
#   1. A per-startup secret token. The dashboard reads it from a <meta> tag in
#      index.html (same-origin only — a cross-origin page cannot read the DOM)
#      and echoes it as `X-Bridge-Token`. A cross-origin attacker cannot learn
#      it, so cannot forge an authorized request.
#   2. An Origin allowlist. The Chrome extension is a separate origin
#      (chrome-extension://<id>) and authenticates by Origin alone (no token).
#
# A Host-header check additionally defeats DNS-rebinding (where a malicious
# domain resolves to 127.0.0.1 to bypass the localhost boundary).

# Fresh per process start; never persisted.
BRIDGE_TOKEN = secrets.token_urlsafe(32)

# Populated in main() once the port is known.
DASHBOARD_ORIGINS: frozenset[str] = frozenset()

# Optional pin for the extension's origin (e.g.
# "chrome-extension://abcdefghijklmnopabcdefghijklmnop"). When unset, any
# chrome-extension:// origin is accepted — acceptable for a single-user local
# tool, since a malicious *installed* extension already has far broader
# capabilities than reaching localhost. Set EXTENSION_ORIGIN in .env to pin it.
_EXTENSION_ORIGIN = os.environ.get("EXTENSION_ORIGIN", "").strip()

# Placeholder substituted with BRIDGE_TOKEN when index.html is served.
_TOKEN_PLACEHOLDER = "__BRIDGE_TOKEN__"

# Content-Security-Policy for the dashboard document. `script-src 'self'`
# (no 'unsafe-inline') neutralises injected <script> and inline event
# handlers even if markdown sanitisation is somehow bypassed.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


def _is_extension_origin(origin: str) -> bool:
    """True if `origin` is the (optionally pinned) Chrome-extension origin."""
    if not origin or not origin.startswith("chrome-extension://"):
        return False
    if _EXTENSION_ORIGIN:
        return origin == _EXTENSION_ORIGIN
    return True


def _origin_allowed(origin: str | None) -> bool:
    """True if `origin` may read responses / send authorized requests."""
    if not origin:
        return False
    return origin in DASHBOARD_ORIGINS or _is_extension_origin(origin)


# ---------------------------------------------------------------------------
# Security: vault-confined Write/Edit via a lockdown settings file
# ---------------------------------------------------------------------------
#
# The CLI --allowedTools flag cannot path-scope Write/Edit (verified: a
# `Write(<path>/**)` entry on the flag matches nothing and denies all writes,
# while bare `Write` allows writing anywhere — e.g. ~/.zshrc, ~/.claude). The
# Claude Code *settings file* format DOES honour path patterns, so we hand the
# skill subprocess a minimal settings file via `--settings` that allows
# Write/Edit only under the vault. Together with --disallowedTools (Bash, net,
# subagents) this bounds what a prompt-injection in imported content can do:
# no shell, no network egress, and file writes confined to this folder.
#
# Path format: the settings matcher denotes an absolute path with a leading
# `//` (mirroring the `Read(//tmp/**)` rule Claude Code itself writes), so we
# prepend one extra slash to VAULT_ROOT (which already starts with `/`).

_LOCKDOWN_SETTINGS_PATH = DASHBOARD_DIR / ".lockdown-settings.json"


def _ensure_lockdown_settings() -> None:
    """(Re)write the lockdown settings file that confines Write/Edit to the vault."""
    cfg = {
        "permissions": {
            "allow": [
                f"Write(/{VAULT_ROOT}/**)",
                f"Edit(/{VAULT_ROOT}/**)",
            ]
        }
    }
    _LOCKDOWN_SETTINGS_PATH.write_text(json.dumps(cfg, indent=2))


# Written once at import so run_claude always has it (covers test entry points
# that call run_claude without going through main()).
_ensure_lockdown_settings()


# ---------------------------------------------------------------------------
# Cross-engine skills: expose the canonical .claude/skills/ tree to Codex
# ---------------------------------------------------------------------------
#
# Claude Code reads skills from .claude/skills/; Codex reads them from
# .agents/skills/. Rather than duplicate (and drift) the SKILL.md files, we
# keep .claude/skills/ as the single source of truth and point .agents/skills
# at it. Both tools resolve the link transparently.
#
# The link is created at runtime (never committed): git cannot represent a
# Windows junction, and a committed POSIX symlink breaks on Windows clones
# (core.symlinks defaults to false there). So .agents/skills is gitignored and
# (re)created here on every start.

_CLAUDE_SKILLS_DIR = VAULT_ROOT / ".claude" / "skills"
_AGENTS_SKILLS_DIR = VAULT_ROOT / ".agents" / "skills"


def _ensure_skills_link() -> None:
    """Make .agents/skills resolve to .claude/skills (for Codex).

    POSIX → symlink; Windows → directory junction (mklink /J, no admin needed);
    last-resort copy only where neither works (non-NTFS / network volume).
    Idempotent and best-effort: a failure here only affects the Codex engine,
    so it never blocks startup.
    """
    src = _CLAUDE_SKILLS_DIR
    link = _AGENTS_SKILLS_DIR
    if not src.is_dir():
        return  # nothing to link (unexpected, but don't crash the bridge)

    try:
        if link.exists():
            # Correct link/junction already in place? Leave it.
            try:
                if link.resolve() == src.resolve():
                    return
            except OSError:
                pass
            if link.is_symlink():
                link.unlink()  # our own stale symlink — safe to drop the link
            else:
                # A real dir or junction we can't verify. Never rmtree it (on
                # Windows that could follow a junction and delete the *target*
                # skills). Leave whatever is there.
                return

        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            # Directory junction: transparent to both tools, no admin/Dev Mode.
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(src)],
                shell=False, capture_output=True, text=True, check=False,
            )
            if not link.exists():
                # Junctions unsupported here (non-NTFS / network) → copy.
                # Refreshed on each start since we only reach this when no
                # link exists yet.
                shutil.copytree(src, link)
        else:
            os.symlink(os.path.relpath(src, link.parent), link, target_is_directory=True)
    except (OSError, subprocess.SubprocessError):
        # Best-effort: Codex falls back to its own discovery / the user can
        # link manually. Claude is unaffected (it reads .claude/skills direct).
        pass


_ensure_skills_link()

# ---------------------------------------------------------------------------
# Skill prompt templates — the only KB-aware code in this file.
#
# Each entry has:
#   build      : callable (args_dict) -> prompt_str, raises KeyError on missing args
#   timeout    : seconds for subprocess.run
#   output_glob: pattern inside outputs/ to look for after the call, or None
#   created_in : subdir of raw/ where the skill writes new files, or None
#   args_required: keys the page must send (validated non-empty strings)
# ---------------------------------------------------------------------------


def _shell_quote(s: str) -> str:
    """Quote a string for safe inclusion inside a double-quoted argv segment.

    The prompt is passed to `claude` as a single argv entry, so the only
    concern is the double-quote characters that already wrap the value inside
    the prompt template. We escape `\\` and `"` so the model sees a clean
    literal string regardless of what the user typed.
    """

    return s.replace("\\", "\\\\").replace('"', '\\"')


def _build_query(args: dict) -> str:
    return f'/second-brain-query "{_shell_quote(args["question"])}"'


def _build_md_add(args: dict) -> str:
    body = args["markdown"]
    title = (args.get("title_hint") or "").strip()
    if title:
        return f'/second-brain-import-md "{_shell_quote(title)}" {body}'
    return f"/second-brain-import-md {body}"


def _build_craft_import(args: dict) -> str:
    folder = args["folder"].strip()
    document = args["document"].strip()
    return f'/second-brain-import-craft "{_shell_quote(f"{folder}/{document}")}"'


def _build_pdf_import(args: dict) -> str:
    # `pdf_path` is set by the bridge after the file is staged to .uploads/.
    path = args["pdf_path"]
    context = (args.get("context") or "").strip()
    if context:
        return (
            f'/second-brain-import-pdf "{_shell_quote(path)}" '
            f'--context "{_shell_quote(context)}"'
        )
    return f'/second-brain-import-pdf "{_shell_quote(path)}"'


def _build_file_import(args: dict) -> str:
    # `file_path` is set by the bridge after the file is staged to .uploads/.
    path = args["file_path"]
    context = (args.get("context") or "").strip()
    if context:
        return (
            f'/second-brain-import-file "{_shell_quote(path)}" '
            f'--context "{_shell_quote(context)}"'
        )
    return f'/second-brain-import-file "{_shell_quote(path)}"'


_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_TEXT_EXTS  = frozenset({".txt", ".md"})
# .pptx is imported deterministically in-process (see _run_pptx_import), NOT via
# a skill — but it must be accepted here so the upload is staged.
ACCEPTED_FILE_EXTS = frozenset({".pdf", ".pptx"}) | _IMAGE_EXTS | _TEXT_EXTS


def _file_import_subdir(file_path: str) -> str:
    """Return the raw/ subdirectory for a given file type ('' = raw root)."""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".pptx":
        return "pptx"
    if ext in _IMAGE_EXTS:
        return "images"
    return ""   # .txt / .md → raw/ root


def _build_wiki_edit(args: dict) -> str:
    prompt = args["prompt"]
    slug = args.get("slug", "")
    if slug:
        return f'/second-brain-edit-wiki "{_shell_quote(prompt)}" "{_shell_quote(slug)}"'
    return f'/second-brain-edit-wiki "{_shell_quote(prompt)}"'


def _build_web_import(args: dict) -> str:
    """Build the slash-command prompt for web-import.

    Two modes (decided by presence of optional `pasted_markdown`):
      - URL only: `/second-brain-import-web "<url>"` → skill fetches via WebFetch.
      - Paste fallback: appends `--pasted-markdown "<body>"` → skill skips fetch,
        uses the body verbatim. The URL is still required and becomes `source:`.

    An optional `context` note (from the dashboard field or the Chrome
    extension) is appended as `--context "<text>"` and composes with either
    mode; the skill embeds it as a Document Context block.
    """

    url = args["url"].strip()
    pasted = (args.get("pasted_markdown") or "").strip()
    context = (args.get("context") or "").strip()[:2000]
    if pasted:
        cmd = (
            f'/second-brain-import-web "{_shell_quote(url)}" '
            f'--pasted-markdown "{_shell_quote(pasted)}"'
        )
    else:
        cmd = f'/second-brain-import-web "{_shell_quote(url)}"'
    if context:
        cmd += f' --context "{_shell_quote(context)}"'
    return cmd


def _build_thread_reply(args: dict) -> str:
    thread_file = str(args["thread_file"]).strip()
    tf_path = Path(thread_file)
    if ".." in tf_path.parts or not thread_file.startswith("outputs/"):
        raise ValueError(f"invalid thread_file: {thread_file!r}")
    return (
        f'/second-brain-follow-up --thread "{_shell_quote(thread_file)}" '
        f'"{_shell_quote(args["question"])}"'
    )


def _build_ingest(_args: dict) -> str:
    return "/second-brain-ingest"


def _build_lint(_args: dict) -> str:
    return "/second-brain-lint"


# ---------------------------------------------------------------------------
# Per-skill tool allow-lists (Layer 2 — blast-radius containment)
#
# Each skill runs with ONLY the tools it needs. Two mechanisms work together,
# because Claude Code UNIONS allow-rules from --allowedTools with the project's
# settings.local.json — an allow-list alone cannot exclude a tool the settings
# file already permits:
#
#   * --allowedTools (below) pre-authorises the needed tools so the headless
#     `-p` run never blocks on a permission prompt. Write/Edit are PATH-SCOPED
#     to the vault, so a prompt-injection in imported content cannot use them to
#     overwrite files elsewhere (e.g. ~/.zshrc, ~/.claude/settings.json). This
#     confinement only holds if settings.local.json grants no *bare* Write/Edit
#     (see the security note in dashboard/README.md) — verified by test.
#   * --disallowedTools (below) DENIES the dangerous tools outright. Deny rules
#     beat every allow rule (CLI flag, project settings, or user-global
#     settings), so this is the authoritative guarantee that no skill can run
#     Bash, reach the network (except web-import's WebFetch), or spawn a
#     subagent to escape the sandbox — regardless of ambient settings.
#
# The Write tool auto-creates parent directories, so no skill needs Bash to
# `mkdir`. Sets derived from each skill's SKILL.md and confirmed by running
# each skill headlessly through the bridge.
# ---------------------------------------------------------------------------

# NOTE on Write/Edit: these are granted (vault-scoped) via the --settings
# lockdown file, NOT here. The CLI --allowedTools flag cannot path-scope
# Write/Edit — `Write(<path>/**)` on the flag matches nothing (denies all
# writes), while bare `Write` allows writing ANYWHERE. Only the settings-file
# format honours path patterns. So the allow-lists below carry just the
# read/network/MCP tools; _LOCKDOWN_SETTINGS adds vault-confined Write/Edit.

# Read-only research + a single output file (query, lint).
_TOOLS_READ_REPORT = ["Read", "Glob", "Grep"]
# Read raw, create/update wiki articles in place (ingest, wiki-edit).
_TOOLS_WIKI_WRITE = ["Read", "Glob", "Grep"]
# Read a source + write one raw file (md/pdf/file imports).
_TOOLS_IMPORT = ["Read", "Glob"]

# Tools no skill needs and that an injected prompt could abuse to break out:
# arbitrary execution (Bash), network egress (WebFetch), or subagent spawning
# (Agent/Workflow). Denied on every skill; web-import overrides to keep WebFetch.
_DENY_DEFAULT = ["Bash", "WebFetch", "Agent", "Workflow"]
_DENY_WEB = ["Bash", "Agent", "Workflow"]  # web-import legitimately needs WebFetch


PROMPT_TEMPLATES: dict[str, dict] = {
    "query": {
        "build": _build_query,
        "timeout": 180,
        "output_glob": "*query*.md",
        "created_in": None,
        "args_required": ["question"],
        "allowed_tools": _TOOLS_READ_REPORT,
        "disallowed_tools": _DENY_DEFAULT,
        # Last-resort lookup when the skill short-circuits without echoing
        # the file path (same question previously answered today).
        "fallback_finder": lambda args: _find_query_file_by_slug(args["question"]),
    },
    # thread-start: same skill as query but writes thread-format output.
    # The frontend navigates to the output viewer after success; skip reading
    # the file back into `result` since the viewer fetches it directly.
    "thread-start": {
        "build": _build_query,
        "timeout": 180,
        "output_glob": "*thread*.md",
        "created_in": None,
        "args_required": ["question"],
        "allowed_tools": _TOOLS_READ_REPORT,
        "disallowed_tools": _DENY_DEFAULT,
        "skip_file_read": True,
    },
    # thread-reply: appends a follow-up turn to an existing thread file.
    # The thread_file path is known from the request args; no glob discovery.
    "thread-reply": {
        "build": _build_thread_reply,
        "timeout": 180,
        "output_glob": None,
        "created_in": None,
        "args_required": ["question", "thread_file"],
        "allowed_tools": _TOOLS_READ_REPORT,
        "disallowed_tools": _DENY_DEFAULT,
        "skip_file_read": True,
        "fallback_finder": lambda args: args.get("thread_file"),
    },
    "md-add": {
        "build": _build_md_add,
        "timeout": 180,
        "output_glob": None,
        "created_in": "",  # raw/ root (paste imports)
        "args_required": ["markdown"],
        "allowed_tools": _TOOLS_IMPORT,
        "disallowed_tools": _DENY_DEFAULT,
    },
    "craft-import": {
        "build": _build_craft_import,
        "timeout": 1800,  # Craft fetches over MCP can be slow; match pdf/file imports
        "output_glob": None,
        "created_in": "craft",
        "args_required": ["folder", "document"],
        # MCP craft reader + read tools; vault-scoped Write via lockdown settings.
        "allowed_tools": ["mcp__claude_ai_Craft__craft_read", "Read", "Glob"],
        "disallowed_tools": _DENY_DEFAULT,
    },
    "pdf-import": {
        "build": _build_pdf_import,
        # The PDF-import skill paginates Reads at ~20 pages per batch and
        # observed throughput is ~30 s/page on this hardware. 1800 s covers
        # roughly a 60-page PDF; only a truly hung skill should hit it.
        "timeout": 1800,
        "output_glob": None,
        "created_in": "pdf",
        # `pdf_path` is injected by /upload-pdf, not sent by the page directly.
        "args_required": ["pdf_path"],
        "allowed_tools": _TOOLS_IMPORT,
        "disallowed_tools": _DENY_DEFAULT,
    },
    "web-import": {
        "build": _build_web_import,
        # WebFetch is fast; the slow step is the model thinking on a long article.
        # 240 s covers a 10 000-word essay without hiding hangs.
        "timeout": 240,
        "output_glob": None,
        "created_in": "web",
        # `pasted_markdown` is optional; the builder branches on its presence.
        "args_required": ["url"],
        "allowed_tools": ["WebFetch", "Read", "Glob"],
        "disallowed_tools": _DENY_WEB,
        # Codex has no WebFetch tool; it fetches via shell, which the
        # workspace-write sandbox blocks by default. Grant network for this op.
        "codex_network": True,
    },
    "file-import": {
        "build": _build_file_import,
        # Same generous timeout as pdf-import: images and long PDFs both need it.
        "timeout": 1800,
        "output_glob": None,
        "created_in": None,           # overridden dynamically via created_in_fn
        "created_in_fn": _file_import_subdir,  # called at run-time with file_path
        "args_required": ["file_path"],
        "allowed_tools": _TOOLS_IMPORT,
        "disallowed_tools": _DENY_DEFAULT,
    },
    "wiki-edit": {
        "build": _build_wiki_edit,
        "timeout": 180,
        "output_glob": None,
        "created_in": None,  # writes to wiki/, not raw/
        "args_required": ["prompt"],
        "allowed_tools": _TOOLS_WIKI_WRITE,
        "disallowed_tools": _DENY_DEFAULT,
    },
    "ingest": {
        "build": _build_ingest,
        "timeout": 600,  # batch ingest of dozens of files can take a while
        "output_glob": None,
        "created_in": None,  # ingest writes to wiki/, not raw/
        "args_required": [],
        "allowed_tools": _TOOLS_WIKI_WRITE,
        "disallowed_tools": _DENY_DEFAULT,
    },
    "lint": {
        "build": _build_lint,
        "timeout": 600,
        "output_glob": "*lint*.md",
        "created_in": None,
        "args_required": [],
        "allowed_tools": _TOOLS_READ_REPORT,
        "disallowed_tools": _DENY_DEFAULT,
    },
}

# ---------------------------------------------------------------------------
# Long-operations mutex
# ---------------------------------------------------------------------------


class Busy(Exception):
    """Raised when another long operation is already in flight."""

    def __init__(self, in_flight: dict):
        super().__init__("busy")
        self.in_flight = in_flight


_lock = threading.Lock()
_in_flight: dict | None = None


@contextmanager
def long_op(kind: str):
    """Acquire the global long-operation mutex without blocking.

    Raises Busy if another long op is in progress.
    """

    global _in_flight
    acquired = _lock.acquire(blocking=False)
    if not acquired:
        raise Busy(_in_flight or {"kind": "unknown"})
    try:
        _in_flight = {"kind": kind, "started_at": _now_iso()}
        yield
    finally:
        _in_flight = None
        _lock.release()


# ---------------------------------------------------------------------------
# Subprocess: run claude -p
# ---------------------------------------------------------------------------


def run_claude(
    prompt: str,
    timeout: int,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
) -> tuple[int, dict]:
    """Exec `claude -p <prompt> --output-format json ...`.

    Returns (status_code, body_dict). status_code is 200 on success, 504 on
    timeout, 502 on spawn failure. The body always parses cleanly as JSON
    for the page.
    """

    # No `--permission-mode bypassPermissions`. In non-interactive `-p` mode,
    # tools absent from `--allowedTools` are denied outright (there is no human
    # to prompt), so `--allowedTools` acts as a hard allow-list. This bounds the
    # blast radius of a prompt-injection attack carried in imported content:
    # e.g. read-only skills get no Bash and no Write, and no skill can act
    # outside the tools it needs. `--add-dir`/`cwd` keep file tools inside the
    # vault. Each skill MUST declare `allowed_tools`; without it the skill can
    # use no tools and will fail loudly rather than run unconstrained.
    argv = [
        CLAUDE_BIN,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--add-dir",
        str(VAULT_ROOT),
        # Lockdown settings: vault-confined Write/Edit (path-scoping the CLI
        # flag can't express). Deny rules below still override everything.
        "--settings",
        str(_LOCKDOWN_SETTINGS_PATH),
    ]
    _t = _CLAUDE_MODEL_TIER
    if _t and _t != "default" and _t in _CLAUDE_TIER_MAP:
        argv += ["--model", _CLAUDE_TIER_MAP[_t]]
    if allowed_tools:
        argv += ["--allowedTools", ",".join(allowed_tools)]
    if disallowed_tools:
        # Deny rules override every allow rule (this flag, project settings, or
        # user-global settings) — the authoritative blast-radius guarantee.
        argv += ["--disallowedTools", ",".join(disallowed_tools)]
    try:
        cp = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            cwd=str(VAULT_ROOT),
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return 502, {"error": "spawn_failed", "detail": str(exc)}
    except subprocess.TimeoutExpired:
        return 504, {"error": "timeout", "after_seconds": timeout}

    stdout = cp.stdout or ""
    stderr = cp.stderr or ""

    try:
        body = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        # The CLI didn't return JSON; surface the raw text so the user sees
        # what went wrong rather than a generic error.
        return 200, {
            "is_error": True,
            "result": stdout or stderr or "(no output)",
            "_bridge_note": "claude did not return valid JSON",
        }

    if cp.returncode != 0 and not body.get("is_error"):
        body.setdefault("is_error", True)
        if not body.get("result"):
            body["result"] = stderr or f"claude exited with code {cp.returncode}"

    return 200, body


def _to_codex_prompt(prompt: str) -> str:
    """Translate a Claude slash-command prompt to Codex skill-invocation syntax.

    The skill name and all arguments are identical; only the leading sigil
    differs — Claude routes `/second-brain-query "…"`, Codex routes
    `$second-brain-query "…"`. The argument text is the same and the skill
    instructions parse it the same way ("extract the question from the
    argument string"), so a one-character swap is the whole translation.
    """
    if prompt.startswith("/"):
        return "$" + prompt[1:]
    return prompt


def run_codex(prompt: str, cfg: dict) -> tuple[int, dict]:
    """Exec `codex exec "$skill …"` and return (status_code, body_dict).

    Mirrors run_claude's contract: 200 on success (body has `result`/`is_error`),
    504 on timeout, 502 on spawn failure. Confinement here is by sandbox +
    working root rather than per-tool allow/deny: every op writes into the vault
    (outputs/, raw/, wiki/), so all run under `--sandbox workspace-write -C
    <vault>`, which limits writes to the vault and disables network by default.
    Codex's final assistant message is captured via `-o <file>` (clean
    equivalent of Claude's `.result`), avoiding JSONL event parsing.
    """
    timeout = cfg.get("timeout")
    codex_prompt = _to_codex_prompt(prompt)

    last_msg_fd, last_msg_path = tempfile.mkstemp(prefix="codex-out-", suffix=".txt")
    os.close(last_msg_fd)
    try:
        argv = [
            CODEX_BIN,
            "exec",
            codex_prompt,
            "-C", str(VAULT_ROOT),
            "--skip-git-repo-check",
            "--sandbox", "workspace-write",
            "-o", last_msg_path,
        ]
        if cfg.get("codex_network"):
            # web-import needs egress; workspace-write blocks it by default.
            argv += ["-c", "sandbox_workspace_write.network_access=true"]
        _t = _CODEX_MODEL_TIER
        if _t and _t != "default" and _t in _CODEX_TIER_MAP:
            argv += ["--model", _CODEX_TIER_MAP[_t]]

        try:
            cp = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                cwd=str(VAULT_ROOT),
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            return 502, {"error": "spawn_failed", "detail": str(exc)}
        except subprocess.TimeoutExpired:
            return 504, {"error": "timeout", "after_seconds": timeout}

        try:
            result_text = Path(last_msg_path).read_text().strip()
        except OSError:
            result_text = ""
    finally:
        try:
            os.unlink(last_msg_path)
        except OSError:
            pass

    stderr = cp.stderr or ""
    body: dict = {"result": result_text or stderr or "(no output)"}
    if cp.returncode != 0:
        body["is_error"] = True
        if not result_text:
            body["result"] = stderr or f"codex exited with code {cp.returncode}"
    return 200, body


def run_skill(prompt: str, cfg: dict) -> tuple[int, dict]:
    """Dispatch a built skill prompt to the configured engine (claude|codex)."""
    if AGENT_ENGINE == "codex":
        return run_codex(prompt, cfg)
    return run_claude(
        prompt,
        timeout=cfg["timeout"],
        allowed_tools=cfg.get("allowed_tools"),
        disallowed_tools=cfg.get("disallowed_tools"),
    )


# ---------------------------------------------------------------------------
# Filesystem snapshot helpers (for output_file discovery)
# ---------------------------------------------------------------------------


def _snapshot(dir_: Path, pattern: str = "**/*") -> set[Path]:
    if not dir_.exists():
        return set()
    return {p for p in dir_.glob(pattern) if p.is_file()}


_OUTPUTS_PATH_RE = re.compile(
    r"`?(outputs/[^\s`)\"']+\.md)`?",
    re.IGNORECASE,
)

# Allowlist for slugs / filenames passed as URL path segments.
# Permits letters, digits, hyphens, underscores, and dots (for .md extensions).
# Critically, it does NOT allow "/" or "\", so path-traversal is impossible.
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")


def _extract_outputs_path(text: str) -> str | None:
    """Find the first reference to an outputs/<file>.md path in the text."""

    if not text:
        return None
    m = _OUTPUTS_PATH_RE.search(text)
    if not m:
        return None
    rel = m.group(1)
    candidate = (VAULT_ROOT / rel).resolve()
    try:
        candidate.relative_to(OUTPUTS_DIR.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return rel


def _slugify_question(text: str, max_len: int = 50) -> str:
    """Lowercase, alphanumeric+hyphen, truncated. Used both for prefix
    matching and as a normalisation step on file names."""

    s = text.lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len].rstrip("-")


def _find_query_file_by_slug(question: str) -> str | None:
    """Locate the saved file for a question when the skill short-circuited
    with an ack that doesn't include the file path.

    The skill derives its filename slug from the question's *first heading
    or first line*, not the whole question — so the bridge can't compute
    the exact slug. Strategy: read each `*_query-*.md` file's `# Query: …`
    heading and find one whose heading the supplied question starts with
    (case-insensitive, after slugifying both sides). This handles both
    "long question, short title" and exact matches.
    """

    if not OUTPUTS_DIR.exists():
        return None
    q_slug = _slugify_question(question)
    if not q_slug:
        return None

    candidates: list[tuple[float, Path]] = []
    for p in OUTPUTS_DIR.glob("*_query-*.md"):
        if not p.is_file():
            continue
        try:
            head = p.read_text().splitlines()[:2]
        except OSError:
            continue
        if not head or not head[0].startswith("# Query: "):
            continue
        title = head[0][len("# Query: "):].strip()
        title_slug = _slugify_question(title, max_len=200)
        if not title_slug:
            continue
        # Either side starts with the other (handles short-title /
        # long-question and identical-question cases).
        if q_slug.startswith(title_slug) or title_slug.startswith(q_slug):
            candidates.append((p.stat().st_mtime, p))

    if not candidates:
        return None
    # Most recent match wins (same question asked across multiple days).
    candidates.sort(reverse=True)
    return str(candidates[0][1].relative_to(VAULT_ROOT))


def _read_saved_output(rel_path: str) -> str | None:
    """Read a file under outputs/ (relative to vault) and return its body.

    Strips the canonical `# Query: ...` heading and `*Date: YYYY-MM-DD*` line
    so the dashboard renders the same content the user typed without the
    skill's metadata wrapper. Returns None on any error.
    """

    try:
        p = (VAULT_ROOT / rel_path).resolve()
        # Refuse to read anything outside outputs/.
        p.relative_to(OUTPUTS_DIR.resolve())
        text = p.read_text()
    except (OSError, ValueError):
        return None

    lines = text.split("\n")
    # Drop a leading "# Query: ..." heading and the "*Date: ...*" line that
    # immediately follows (with optional blank lines between them). Be
    # conservative — only strip when both signatures are present.
    start = 0
    if start < len(lines) and lines[start].startswith("# Query: "):
        start += 1
        while start < len(lines) and lines[start].strip() == "":
            start += 1
        if start < len(lines) and lines[start].startswith("*Date:"):
            start += 1
            while start < len(lines) and lines[start].strip() == "":
                start += 1
    return "\n".join(lines[start:]).strip()


def _newest_match(dir_: Path, glob: str, exclude: set[Path]) -> str | None:
    """Return the newest file matching glob that was NOT in `exclude`.

    The exclude set is captured before the skill runs; matches must be files
    the skill actually created (or rewrote — an in-place rewrite changes
    mtime but does not change identity, so we also accept members of exclude
    whose mtime is newer than the snapshot would have been). For now, the
    conservative behaviour is: only return paths newly created during this
    call. If no such file exists, return None — never surface a stale match.
    """

    if not dir_.exists():
        return None
    new_matches = [p for p in dir_.glob(glob) if p.is_file() and p not in exclude]
    if not new_matches:
        return None
    newest = max(new_matches, key=lambda p: p.stat().st_mtime)
    return str(newest.relative_to(VAULT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cors_header(handler: http.server.BaseHTTPRequestHandler) -> None:
    """Echo Access-Control-Allow-Origin only for allowlisted origins.

    Reflecting `*` (the old behaviour) let any web page read the response —
    an info-disclosure leak of wiki/query content. We echo the specific
    request Origin only when it is allowed, and omit the header otherwise so
    the browser blocks the cross-origin read.
    """
    origin = handler.headers.get("Origin")
    if _origin_allowed(origin):
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")


def _json_response(handler: http.server.BaseHTTPRequestHandler, status: int, body: dict) -> None:
    payload = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    _cors_header(handler)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(payload)


def _safe_static_path(url_path: str) -> Path | None:
    """Resolve a /static/... URL to a path inside dashboard/, or None."""

    if not url_path.startswith("/static/"):
        return None
    rel = url_path[len("/static/"):]
    if not rel or rel.startswith("/"):
        return None
    candidate = (DASHBOARD_DIR / rel).resolve()
    try:
        candidate.relative_to(DASHBOARD_DIR.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _format_prompt(kind: str, args: dict) -> tuple[str | None, dict | None]:
    """Return (prompt, None) on success or (None, error_dict) on failure."""

    cfg = PROMPT_TEMPLATES[kind]
    for required in cfg["args_required"]:
        value = args.get(required)
        if not isinstance(value, str) or not value.strip():
            return None, {
                "error": "bad_request",
                "detail": f"missing or empty arg: {required}",
            }
    try:
        prompt = cfg["build"](args)
    except KeyError as exc:
        return None, {"error": "bad_request", "detail": f"missing arg: {exc}"}
    except ValueError as exc:
        return None, {"error": "bad_request", "detail": str(exc)}
    return prompt, None


# ---------------------------------------------------------------------------
# Vault status (filesystem-only, never spawns claude)
# ---------------------------------------------------------------------------
#
# `GET /status` returns a snapshot of vault counts and last-ingest time.
# Source of truth for "pending":
#   - `raw/.ingest-manifest.json` has shape: { "<rel-path>": {last_modified, ingested_at}, ... }
#   - A user-facing raw file is "pending" iff:
#       (a) it is not in the manifest at all, OR
#       (b) its current filesystem mtime > the manifest's ingested_at
# "User-facing raw files" are: raw/*.md, raw/pdf/**/*.md, raw/craft/**/*.md.
# We deliberately ignore *.assets/ contents and the manifest itself.

WIKI_DIR = VAULT_ROOT / "wiki"
INGEST_MANIFEST = RAW_DIR / ".ingest-manifest.json"

# Tolerate small clock skew between filesystem mtime and manifest timestamps
# (e.g. when the skill writes the manifest a fraction of a second before the
# file is closed). Without this, files would always look "pending" by a few
# hundred ms after every ingest.
_MTIME_SLACK = timedelta(seconds=1)


def _raw_user_files() -> dict[str, Path]:
    """Return {relative_path: Path} for every user-facing file in raw/."""

    out: dict[str, Path] = {}
    if not RAW_DIR.exists():
        return out
    for p in RAW_DIR.rglob("*"):
        if not p.is_file():
            continue
        # Skip dotfiles (.DS_Store, .ingest-manifest.json, etc.) at any level.
        if any(part.startswith(".") for part in p.relative_to(RAW_DIR).parts):
            continue
        rel = p.relative_to(VAULT_ROOT).as_posix()
        # Skip image/asset attachments — the manifest tracks them but they
        # are not user-facing "raw items".
        if ".assets/" in rel or rel.endswith(".assets"):
            continue
        out[rel] = p
    return out


def _load_ingest_manifest() -> tuple[dict, bool]:
    """Read raw/.ingest-manifest.json, degrading to ({}, False) on any error."""

    if not INGEST_MANIFEST.exists():
        return {}, False
    try:
        manifest = json.loads(INGEST_MANIFEST.read_text())
    except (OSError, json.JSONDecodeError):
        return {}, False
    if not isinstance(manifest, dict):
        return {}, False
    return manifest, True


def _raw_pending_count(manifest: dict, manifest_ok: bool,
                       raw_files: dict[str, Path]) -> int:
    """Number of user-facing raw files awaiting (re)ingestion.

    Staleness rule (matches the ingest skill's SKILL.md): a file is pending iff it
    has no manifest entry, was never confirmed ingested, or its recorded
    `last_modified` is later than its `ingested_at`. We compare the manifest's own
    recorded timestamps — never the live filesystem mtime — so a git checkout /
    branch switch (which rewrites mtimes without changing content) never inflates
    the count. No manifest = everything is pending.
    """

    if not manifest_ok:
        return len(raw_files)
    pending = 0
    for rel in raw_files:
        entry = manifest.get(rel)
        if not isinstance(entry, dict):
            pending += 1  # no manifest entry → genuinely new
            continue
        ingested_at_str = entry.get("ingested_at")
        if not ingested_at_str:
            pending += 1  # never confirmed ingested
            continue
        try:
            ingested_at = datetime.fromisoformat(ingested_at_str.replace("Z", "+00:00"))
        except ValueError:
            pending += 1
            continue
        last_modified_str = entry.get("last_modified")
        if not last_modified_str:
            continue  # recorded as ingested, no known later change → current
        try:
            last_modified = datetime.fromisoformat(last_modified_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if last_modified > ingested_at + _MTIME_SLACK:
            pending += 1  # edited after it was last ingested
    return pending


def _vault_status() -> dict:
    """Build the VaultStatus payload. Never raises — degraded fields are null."""

    # wiki article count
    wiki_count = 0
    if WIKI_DIR.exists():
        wiki_count = sum(
            1 for p in WIKI_DIR.glob("*.md") if p.is_file() and p.name != "INDEX.md"
        )

    # outputs counts
    def _glob_count(pattern: str) -> int:
        if not OUTPUTS_DIR.exists():
            return 0
        return sum(1 for _ in OUTPUTS_DIR.glob(pattern))

    outputs_query_count = _glob_count("*query*.md")
    outputs_lint_count = _glob_count("*lint*.md")

    # raw breakdown — counts of user-facing markdown files.
    paste_count = 0
    pdf_count = 0
    web_count = 0
    craft_count = 0
    if RAW_DIR.exists():
        paste_count = sum(
            1 for p in RAW_DIR.glob("*.md") if p.is_file()
        )
        pdf_dir = RAW_DIR / "pdf"
        if pdf_dir.exists():
            pdf_count = sum(1 for p in pdf_dir.glob("*.md") if p.is_file())
        web_dir = RAW_DIR / "web"
        if web_dir.exists():
            web_count = sum(1 for p in web_dir.glob("*.md") if p.is_file())
        craft_dir = RAW_DIR / "craft"
        if craft_dir.exists():
            craft_count = sum(1 for p in craft_dir.glob("*.md") if p.is_file())

    # Load manifest once (degrade gracefully); reused for pending + last_ingest.
    manifest, manifest_ok = _load_ingest_manifest()

    raw_files = _raw_user_files()
    pending = _raw_pending_count(manifest, manifest_ok, raw_files)

    # last_ingest_iso
    last_ingest_iso: str | None = None
    last_ingest_source = "none"
    if manifest_ok and manifest:
        ts_values: list[datetime] = []
        for entry in manifest.values():
            if not isinstance(entry, dict):
                continue
            s = entry.get("ingested_at")
            if not s:
                continue
            try:
                ts_values.append(datetime.fromisoformat(s.replace("Z", "+00:00")))
            except ValueError:
                continue
        if ts_values:
            last_ingest_iso = max(ts_values).isoformat(timespec="seconds")
            last_ingest_source = "manifest"

    if last_ingest_iso is None:
        index_md = WIKI_DIR / "INDEX.md"
        if index_md.exists():
            last_ingest_iso = datetime.fromtimestamp(
                index_md.stat().st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds")
            last_ingest_source = "mtime"

    return {
        "wiki_article_count": wiki_count,
        # Total user-facing raw files — the same universe pending is measured
        # against, so raw_pending_count is always a subset of this.
        "raw_total_count": len(raw_files),
        "raw_pending_count": pending,
        "raw_breakdown": {
            "paste": paste_count,
            "pdf": pdf_count,
            "web": web_count,
            "craft": craft_count,
        },
        "outputs_query_count": outputs_query_count,
        "outputs_lint_count": outputs_lint_count,
        "last_ingest_iso": last_ingest_iso,
        "last_ingest_source": last_ingest_source,
    }


def _wiki_list() -> list:
    """Return [{slug, title, mtime_iso}] for all wiki articles, sorted by title.

    INDEX.md is excluded from the list (it has its own nav entry) but remains
    accessible via GET /wiki/INDEX.
    """

    if not WIKI_DIR.exists():
        return []
    result = []
    for p in WIKI_DIR.glob("*.md"):
        if not p.is_file() or p.name == "INDEX.md":
            continue
        slug = p.stem
        title = slug.replace("-", " ").title()
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip()
                    break
        except OSError:
            pass
        mtime = datetime.fromtimestamp(
            p.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds")
        result.append({"slug": slug, "title": title, "mtime_iso": mtime})
    result.sort(key=lambda x: x["title"].lower())
    return result


def _outputs_list() -> list:
    """Return [{filename, date_iso, kind, title}] for all outputs, newest first."""

    if not OUTPUTS_DIR.exists():
        return []
    _PAT = re.compile(r"^(\d{4}-\d{2}-\d{2})_(query|lint|thread)(?:-(.+))?\.md$")
    result = []
    for p in OUTPUTS_DIR.glob("*.md"):
        if not p.is_file():
            continue
        m = _PAT.match(p.name)
        if not m:
            continue
        date_str = m.group(1)
        kind = m.group(2)
        slug = m.group(3) or ""
        if kind == "lint":
            title = "Lint report"
        else:
            raw = slug.replace("-", " ")
            fallback = "Thread" if kind == "thread" else "Query"
            title = raw[:1].upper() + raw[1:] if raw else fallback
        result.append({
            "filename": p.name,
            "date_iso": date_str,
            "kind": kind,
            "title": title,
            "_mtime": p.stat().st_mtime,
        })
    result.sort(key=lambda x: x["_mtime"], reverse=True)
    for item in result:
        del item["_mtime"]
    return result


# Keyword search across the two synthesised corpora the user reads: saved
# answers (outputs/) and wiki articles. Plain case-insensitive substring match
# — NOT regex, so query characters are literal and there is no injection
# surface. The vault is tiny (dozens of small markdown files), so scanning on
# every request is instant; no index is warranted.
_SEARCH_MIN_LEN = 2
_SEARCH_MAX_QUERY = 200
_SEARCH_MAX_RESULTS = 40
_SEARCH_SNIPPET_RADIUS = 60  # chars of context on each side of the first hit


def _make_snippet(text: str, lo: int, hi: int) -> str:
    """A one-line window around text[lo:hi], whitespace-collapsed, with ellipses."""
    start = max(0, lo - _SEARCH_SNIPPET_RADIUS)
    end = min(len(text), hi + _SEARCH_SNIPPET_RADIUS)
    window = " ".join(text[start:end].split())  # collapse newlines/runs of space
    if start > 0:
        window = "…" + window
    if end < len(text):
        window = window + "…"
    return window


def _search_vault(q: str) -> list[dict]:
    """Return match records across wiki/ and outputs/ for query `q`.

    Shape: {source: "wiki"|"output", id, title, kind, date_iso, snippet, hits}.
    Never raises — unreadable files are skipped.
    """
    q = (q or "").strip()[:_SEARCH_MAX_QUERY]
    if len(q) < _SEARCH_MIN_LEN:
        return []
    needle = q.lower()

    # (path, source, id, title, kind, date_iso) tuples to scan.
    targets: list[tuple[Path, str, str, str, str | None, str | None]] = []
    for w in _wiki_list():
        targets.append((WIKI_DIR / f"{w['slug']}.md", "wiki", w["slug"], w["title"], None, None))
    for o in _outputs_list():
        targets.append((OUTPUTS_DIR / o["filename"], "output", o["filename"], o["title"], o["kind"], o["date_iso"]))

    results: list[dict] = []
    for path, source, ident, title, kind, date_iso in targets:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hay = text.lower()
        first = hay.find(needle)
        if first < 0:
            continue
        results.append({
            "source": source,
            "id": ident,
            "title": title,
            "kind": kind,
            "date_iso": date_iso,
            "snippet": _make_snippet(text, first, first + len(needle)),
            "hits": hay.count(needle),
        })

    # Most hits first, then title; cap the list.
    results.sort(key=lambda r: (-r["hits"], r["title"].lower()))
    return results[:_SEARCH_MAX_RESULTS]


# ---------------------------------------------------------------------------
# Duplicate-import detection (model-free)
# ---------------------------------------------------------------------------
#
# Every raw file carries a `source:` frontmatter line (a URL, a filesystem path,
# a Craft path, or "pasted"). Before the slow import runs, the dashboard asks
# the bridge whether a candidate (URL / filename / Craft doc / pasted body)
# already looks imported. This is a pure filesystem scan — no model, no cost —
# memoized per (path, mtime) so repeat checks while the user types stay instant.

_TRACKING_PARAMS = re.compile(r"^(?:utm_.*|fbclid|gclid|mc_eid|mc_cid|ref)$", re.I)
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_")
_FM_LINE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
# {rel_path: (mtime, static_entry)} — the static half of a raw index entry
# (everything except the manifest-derived `ingested` flag, which is recomputed
# each call because the manifest changes without the file's mtime changing).
_raw_index_cache: dict[str, tuple[float, dict]] = {}


def _slugify(text: str) -> str:
    """Lowercase; collapse runs of non-alphanumerics to single hyphens; trim.

    Mirrors the import skills' filename slugging closely enough that a candidate
    identifier and an existing file's slug compare equal for the same source.
    """
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _normalize_url(url: str) -> str:
    """Canonical form for URL equality: lowercase scheme+host (drop leading
    ``www.``), drop default port, fragment, tracking params, and a trailing
    slash. Returns "" for values that are not http(s) URLs."""

    url = (url or "").strip()
    if "://" not in url:
        return ""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return ""
    scheme = p.scheme.lower()
    host = p.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    port = ""
    if p.port and not (
        (scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)
    ):
        port = f":{p.port}"
    path = p.path.rstrip("/")
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(p.query)
        if not _TRACKING_PARAMS.match(k)
    ]
    out = f"{scheme}://{host}{port}{path}"
    if kept:
        out += "?" + urllib.parse.urlencode(sorted(kept))
    return out


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a leading ``---`` … ``---`` block of simple ``key: value`` lines.

    Returns (frontmatter, body). Absent/malformed frontmatter → ({}, text).
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    fm: dict[str, str] = {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
        m = _FM_LINE.match(lines[i])
        if m:
            fm[m.group(1).lower()] = m.group(2).strip()
    if end is None:
        return {}, text
    return fm, "\n".join(lines[end + 1:])


def _body_sha(body: str) -> str:
    """Whitespace-collapsed, lowercased SHA-256 of a document body — a stable
    fingerprint for exact-content matching that ignores incidental reflow."""
    norm = " ".join((body or "").lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _is_ingested(manifest: dict, manifest_ok: bool, rel: str) -> bool:
    """Whether a raw file has been folded into the wiki and not edited since.

    Mirrors the (inverse of the) staleness rule in _raw_pending_count()."""
    if not manifest_ok:
        return False
    entry = manifest.get(rel)
    if not isinstance(entry, dict):
        return False
    ingested_at_str = entry.get("ingested_at")
    if not ingested_at_str:
        return False
    try:
        ingested_at = datetime.fromisoformat(ingested_at_str.replace("Z", "+00:00"))
    except ValueError:
        return False
    last_modified_str = entry.get("last_modified")
    if not last_modified_str:
        return True
    try:
        last_modified = datetime.fromisoformat(last_modified_str.replace("Z", "+00:00"))
    except ValueError:
        return True
    return last_modified <= ingested_at + _MTIME_SLACK


def _raw_index() -> list[dict]:
    """Lightweight metadata for every raw .md file, memoized by (path, mtime)."""

    manifest, manifest_ok = _load_ingest_manifest()
    files = _raw_user_files()
    out: list[dict] = []
    for rel, p in files.items():
        if not rel.endswith(".md"):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        cached = _raw_index_cache.get(rel)
        if cached and cached[0] == mtime:
            entry = dict(cached[1])
        else:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, body = _parse_frontmatter(text)
            parts = rel.split("/")
            subdir = (
                parts[1]
                if len(parts) >= 3 and parts[1] in ("pdf", "web", "craft", "images", "pptx")
                else "paste"
            )
            slug = _DATE_PREFIX.sub("", parts[-1][:-3])  # drop date prefix + ".md"
            source = fm.get("source", "")
            is_path_source = bool(source) and "://" not in source and source != "pasted"
            # Craft files carry no `title:`; use the document name (minus its
            # trailing "[date]"/"(link)" decorations) rather than the raw slug.
            craft_doc = re.sub(r"\s*[\[(].*$", "", fm.get("craft-document", "")).strip()
            title = fm.get("title", "") or craft_doc or slug.replace("-", " ")
            static = {
                "path": rel,
                "subdir": subdir,
                "title": title,
                "source": source,
                "source_norm": _normalize_url(source),
                "slug": slug,
                "source_base_slug": _slugify(Path(source).stem) if is_path_source else "",
                "craft_folder": fm.get("craft-folder", ""),
                "craft_document": fm.get("craft-document", ""),
                "imported": fm.get("imported", ""),
                "body_sha": _body_sha(body),
            }
            _raw_index_cache[rel] = (mtime, static)
            entry = dict(static)
        entry["ingested"] = _is_ingested(manifest, manifest_ok, rel)
        out.append(entry)
    # Drop cache entries for files that no longer exist.
    live = set(files)
    for stale in [k for k in _raw_index_cache if k not in live]:
        _raw_index_cache.pop(stale, None)
    return out


def _dedupe_match_public(entry: dict, match_type: str, confidence: str) -> dict:
    """Project a raw-index entry to the fields the dashboard warning renders."""
    return {
        "path": entry["path"],
        "subdir": entry["subdir"],
        "title": entry["title"],
        "source": entry["source"],
        "imported": entry["imported"],
        "ingested": entry["ingested"],
        "match_type": match_type,
        "confidence": confidence,
    }


def _dedupe_check(payload: dict) -> dict:
    """Return up to 3 already-imported matches for an import candidate.

    Exact-ish only (URL / content hash / Craft doc / filename slug ⇒ high;
    partial filename or title-only ⇒ medium). No fuzzy/semantic matching.
    """
    t0 = time.monotonic()
    kind = (payload.get("kind") or "").strip()
    index = _raw_index()
    matches: list[dict] = []

    if kind == "web":
        cand = _normalize_url(payload.get("url", ""))
        if cand:
            for e in index:
                if e["source_norm"] and e["source_norm"] == cand:
                    matches.append(_dedupe_match_public(e, "url", "high"))

    elif kind == "file":
        cand = _slugify(Path(payload.get("filename", "")).stem)
        if cand:
            for e in index:
                if cand in (e["slug"], e["source_base_slug"]):
                    matches.append(_dedupe_match_public(e, "filename", "high"))
                elif (
                    e["slug"]
                    and min(len(cand), len(e["slug"])) >= 6
                    and (cand in e["slug"] or e["slug"] in cand)
                ):
                    matches.append(_dedupe_match_public(e, "filename-partial", "medium"))

    elif kind == "craft":
        fslug = _slugify(payload.get("folder", ""))
        dslug = _slugify(payload.get("document", ""))
        for e in index:
            if e["subdir"] != "craft" or not dslug:
                continue
            ed = _slugify(e["craft_document"])
            ef = _slugify(e["craft_folder"])
            if ed and (ed == dslug or dslug in ed or ed in dslug):
                folder_ok = not fslug or not ef or fslug in ef or ef in fslug
                matches.append(
                    _dedupe_match_public(e, "craft-doc", "high" if folder_ok else "medium")
                )
            elif dslug in e["slug"]:
                matches.append(_dedupe_match_public(e, "craft-doc", "medium"))

    elif kind == "md":
        cand_sha = _body_sha(payload["content"]) if payload.get("content", "").strip() else ""
        tslug = _slugify(payload.get("title", ""))
        for e in index:
            if cand_sha and e["body_sha"] == cand_sha:
                matches.append(_dedupe_match_public(e, "content", "high"))
            elif tslug and _slugify(e["title"]) == tslug:
                matches.append(_dedupe_match_public(e, "title", "medium"))

    # High confidence first; de-dupe by path; cap at 3.
    matches.sort(key=lambda m: 0 if m["confidence"] == "high" else 1)
    result: list[dict] = []
    seen: set[str] = set()
    for m in matches:
        if m["path"] in seen:
            continue
        seen.add(m["path"])
        result.append(m)
        if len(result) >= 3:
            break
    return {"matches": result, "checked_ms": int((time.monotonic() - t0) * 1000)}


# ---------------------------------------------------------------------------
# Multipart upload parser (RFC 7578 subset)
# ---------------------------------------------------------------------------
#
# We accept a single file field. We do NOT use cgi.FieldStorage — it was
# removed in Python 3.13 and absent on 3.14. We also do NOT use the
# email.parser machinery, which decodes payloads as text and corrupts
# binary content. Instead: split the raw bytes on the boundary, and read
# the first file part.


class UploadError(Exception):
    pass


def _parse_multipart_pdf(
    content_type: str, raw: bytes
) -> tuple[str, bytes, dict[str, str]]:
    """Extract (filename, body_bytes, fields) from a multipart upload.

    `fields` holds any non-file text parts, keyed by their form field name
    (e.g. an optional "context" note). Raises UploadError(detail) with a
    human-readable message on any failure.
    """

    if not content_type:
        raise UploadError("missing Content-Type")
    if "multipart/form-data" not in content_type.lower():
        raise UploadError("Content-Type must be multipart/form-data")

    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            boundary = part[len("boundary="):].strip().strip('"')
            break
    if not boundary:
        raise UploadError("multipart boundary missing")

    sep = b"--" + boundary.encode("ascii")
    chunks = raw.split(sep)
    filename: str | None = None
    body: bytes = b""
    fields: dict[str, str] = {}
    # chunks[0] is the preamble, last is the closing "--\r\n" — both ignored.
    for chunk in chunks[1:-1]:
        # Each chunk starts with \r\n and ends with \r\n.
        chunk = chunk.lstrip(b"\r\n")
        if not chunk:
            continue
        header_end = chunk.find(b"\r\n\r\n")
        if header_end < 0:
            continue
        headers_blob = chunk[:header_end].decode("utf-8", errors="replace")
        part_body = chunk[header_end + 4 : -2]  # strip trailing \r\n

        disposition = ""
        for line in headers_blob.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                disposition = line.split(":", 1)[1].strip()
                break

        part_name = ""
        part_filename = ""
        for piece in disposition.split(";"):
            piece = piece.strip()
            if piece.lower().startswith("filename="):
                part_filename = piece[len("filename="):].strip().strip('"')
            elif piece.lower().startswith("name="):
                part_name = piece[len("name="):].strip().strip('"')

        if part_filename:
            if filename is None:  # first file part wins
                filename, body = part_filename, part_body
        elif part_name:
            fields[part_name] = part_body.decode("utf-8", errors="replace")

    if filename is None:
        raise UploadError("no file part found in multipart body")
    return filename, body, fields

    raise UploadError("no file part found in multipart body")


def _stage_pdf(filename: str, body: bytes) -> Path:
    """Validate and write the uploaded PDF to dashboard/.uploads/."""

    if not filename.lower().endswith(".pdf"):
        raise UploadError("not a PDF (filename must end in .pdf)")
    if not body.startswith(b"%PDF-"):
        raise UploadError("not a PDF (missing %PDF- header)")
    uploads = DASHBOARD_DIR / ".uploads"
    uploads.mkdir(exist_ok=True)
    # Preserve the original stem so the skill's content-match dedup (which
    # works off the staged filename's slug) recognises a re-upload of the
    # same document as already-imported. Single-user bridge — back-to-back
    # uploads with the same filename are vanishingly unlikely.
    stem = Path(filename).stem or "upload"
    safe_stem = "".join(c for c in stem if c.isalnum() or c in "-_.").strip("-._")
    if not safe_stem:
        safe_stem = "upload"
    target = uploads / f"{safe_stem}.pdf"
    target.write_bytes(body)
    return target


def _stage_file(filename: str, body: bytes) -> Path:
    """Validate and write any accepted file to dashboard/.uploads/."""

    ext = Path(filename).suffix.lower()
    if ext not in ACCEPTED_FILE_EXTS:
        raise UploadError(
            f"unsupported file type: {ext}. Accepted: "
            + ", ".join(sorted(ACCEPTED_FILE_EXTS))
        )
    uploads = DASHBOARD_DIR / ".uploads"
    uploads.mkdir(exist_ok=True)
    stem = Path(filename).stem or "upload"
    safe_stem = "".join(c for c in stem if c.isalnum() or c in "-_.").strip("-._")
    if not safe_stem:
        safe_stem = "upload"
    target = uploads / f"{safe_stem}{ext}"
    target.write_bytes(body)
    return target


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SecondBrainDashboard/0.1"

    # Quiet the default request log; use sys.stderr ourselves below.
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write("[bridge] %s - %s\n" % (self.address_string(), format % args))

    # ----- CORS preflight (Chrome extension → localhost) ------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        # Approve the preflight ONLY for allowlisted origins; for anything else
        # we omit the CORS headers so the browser refuses to send the real
        # request. (The server-side _authorize check is the real guard; this
        # just stops the request earlier.)
        origin = self.headers.get("Origin")
        if _origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Bridge-Token")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ----- Authorization (CSRF gate) --------------------------------------

    def _authorize(self, allow_extension: bool = False) -> bool:
        """Return True if this request may touch vault data / trigger a skill.

        Two independent ways to pass:
          - a valid X-Bridge-Token header (the dashboard, same-origin), or
          - an allowlisted chrome-extension:// Origin (the extension), when
            `allow_extension` is set (state-changing endpoints only).

        A Host-header check rejects DNS-rebinding attempts up front.
        """
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0] if host else ""
        if hostname not in ("localhost", "127.0.0.1"):
            return False

        token = self.headers.get("X-Bridge-Token", "")
        if token and secrets.compare_digest(token, BRIDGE_TOKEN):
            return True

        if allow_extension and _is_extension_origin(self.headers.get("Origin", "")):
            return True

        return False

    def _deny(self) -> None:
        _json_response(self, 403, {"error": "forbidden", "detail": "unauthorized origin or missing bridge token"})

    # ----- GET ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Ungated bootstrap surface: the HTML shell + static assets + liveness.
        # index.html carries the bridge token but is readable only same-origin
        # (a cross-origin page cannot read another origin's DOM), so the token
        # stays secret. Static assets and /healthz expose no vault data.
        if path == "/":
            return self._serve_index()
        if path.startswith("/static/"):
            resolved = _safe_static_path(path)
            if resolved is None:
                return self._not_found()
            return self._serve_file(resolved)
        if path == "/healthz":
            return _json_response(self, 200, {"ok": True})
        if path == "/busy":
            # Lightweight app-state for the native app's Dock indicator: whether a
            # skill is running (from the long-op mutex) and how many raw files await
            # ingestion. Non-sensitive counts/flags; a best-effort read of the module
            # global is fine — any momentary race self-corrects on the next poll.
            snap = _in_flight
            manifest, manifest_ok = _load_ingest_manifest()
            pending = _raw_pending_count(manifest, manifest_ok, _raw_user_files())
            return _json_response(self, 200, {
                "running": snap is not None,
                "kind": (snap or {}).get("kind"),
                "pending": pending,
            })

        # Everything below returns vault data → require authorization.
        if not self._authorize():
            return self._deny()

        if path == "/config":
            return _json_response(self, 200, {
                "vault_root":        str(VAULT_ROOT),
                "craft_enabled":     _CRAFT_ENABLED,
                "engine":            AGENT_ENGINE,
                "claude_model_tier": _CLAUDE_MODEL_TIER,
                "codex_model_tier":  _CODEX_MODEL_TIER,
                "claude_tier_map":   _CLAUDE_TIER_MAP,
                "codex_tier_map":    _CODEX_TIER_MAP,
            })
        if path == "/status":
            return _json_response(self, 200, _vault_status())
        if path == "/wiki":
            return _json_response(self, 200, _wiki_list())
        if path.startswith("/wiki/") and len(path) > 6:
            return self._serve_wiki_article(path[6:])
        if path == "/outputs":
            return _json_response(self, 200, _outputs_list())
        if path.startswith("/outputs/") and len(path) > 9:
            return self._serve_output_file(path[9:])
        if path == "/search":
            params = urllib.parse.parse_qs(parsed.query)
            q = (params.get("q", [""])[0] or "").strip()
            return _json_response(self, 200, {"query": q, "results": _search_vault(q)})
        if path.startswith("/raw/") and len(path) > 5:
            return self._serve_raw_file(path[5:])

        self._not_found()

    # ----- POST -----------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # All POST endpoints trigger a skill (or a cheap read-only check) →
        # require authorization. The extension authenticates by its allowlisted
        # Origin (no token).
        if path in ("/run", "/upload-pdf", "/upload-file", "/dedupe-check",
                    "/open-folder", "/patch-finding", "/set-model"):
            if not self._authorize(allow_extension=True):
                return self._deny()

        if path == "/run":
            return self._handle_run()
        if path == "/upload-pdf":
            return self._handle_upload_pdf()
        if path == "/upload-file":
            return self._handle_upload_file()
        if path == "/dedupe-check":
            return self._handle_dedupe_check()
        if path == "/open-folder":
            return self._handle_open_folder()
        if path == "/patch-finding":
            return self._handle_patch_finding()
        if path == "/set-model":
            return self._handle_set_model()

        self._not_found()

    # ----- DELETE ---------------------------------------------------------

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Deleting a saved output mutates the vault → require authorization.
        if not self._authorize():
            return self._deny()

        if path.startswith("/outputs/") and len(path) > 9:
            return self._delete_output_file(path[9:])

        self._not_found()

    # ----- handlers -------------------------------------------------------

    def _handle_upload_pdf(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return _json_response(
                self, 400, {"error": "bad_request", "detail": "empty body"}
            )
        # Cap at 64 MB to avoid the page accidentally posting something huge.
        if length > 64 * 1024 * 1024:
            return _json_response(
                self,
                413,
                {"error": "too_large", "detail": "PDF over 64 MB not supported"},
            )

        raw = self.rfile.read(length)
        try:
            filename, body, fields = _parse_multipart_pdf(
                self.headers.get("Content-Type", ""), raw
            )
            tempfile_path = _stage_pdf(filename, body)
        except UploadError as exc:
            return _json_response(
                self, 400, {"error": "not_a_pdf", "detail": str(exc)}
            )

        try:
            cfg = PROMPT_TEMPLATES["pdf-import"]
            pdf_args = {
                "pdf_path": str(tempfile_path),
                "context": (fields.get("context") or "").strip()[:2000],
            }
            prompt = cfg["build"](pdf_args)
            envelope = self._run_kind("pdf-import", prompt, cfg, pdf_args)
        finally:
            try:
                tempfile_path.unlink()
            except OSError:
                pass

        status_code = envelope.pop("__status__", 200)
        _json_response(self, status_code, envelope)

    def _handle_upload_file(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return _json_response(
                self, 400, {"error": "bad_request", "detail": "empty body"}
            )
        if length > 64 * 1024 * 1024:
            return _json_response(
                self,
                413,
                {"error": "too_large", "detail": "File over 64 MB not supported"},
            )

        raw = self.rfile.read(length)
        try:
            filename, body, fields = _parse_multipart_pdf(
                self.headers.get("Content-Type", ""), raw
            )
            tempfile_path = _stage_file(filename, body)
        except UploadError as exc:
            return _json_response(
                self, 400, {"error": "bad_file", "detail": str(exc)}
            )

        try:
            context = (fields.get("context") or "").strip()[:2000]
            if Path(filename).suffix.lower() == ".pptx":
                # Deterministic, model-free path (see _run_pptx_import).
                envelope = self._run_pptx_import(tempfile_path, context, filename)
            else:
                cfg = PROMPT_TEMPLATES["file-import"]
                file_args = {
                    "file_path": str(tempfile_path),
                    "context": context,
                }
                prompt = cfg["build"](file_args)
                envelope = self._run_kind("file-import", prompt, cfg, file_args)
        finally:
            try:
                tempfile_path.unlink()
            except OSError:
                pass

        status_code = envelope.pop("__status__", 200)
        _json_response(self, status_code, envelope)

    def _run_pptx_import(self, staged_path, context: str, orig_filename: str) -> dict:
        """Convert a .pptx to markdown in-process and write raw/pptx/<date>_<slug>.md.

        Unlike pdf/image/text imports (which run a skill via `claude -p`), a .pptx
        is deterministic: its content is cached OOXML, so the bridge extracts it
        with the pure-stdlib `pptx_extract` module and writes the raw file itself —
        no model call, near-instant. Returns the same envelope shape as _run_kind.
        """
        # Lazy import so a problem in the module can't block bridge start-up.
        import pptx_extract  # dashboard/ is on sys.path (script dir)

        try:
            with long_op("pptx-import"):
                started = time.time()
                try:
                    data = pptx_extract.pptx_to_markdown(str(staged_path))
                except pptx_extract.PptxError as exc:
                    return {"__status__": 422, "error": "bad_pptx", "detail": str(exc)}

                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                stem = Path(orig_filename).stem
                slug = _slugify(stem)[:60].strip("-") or "deck"
                title = stem.strip() or slug
                rel = f"raw/pptx/{today}_{slug}.md"
                target = VAULT_ROOT / rel

                fm = [
                    "---",
                    f"source: {orig_filename}",
                    f"imported: {today}",
                    f"title: {title}",
                    f"slides: {data['slides']}",
                ]
                if data.get("content_date"):
                    fm.append(f"content_date: {data['content_date']}")
                fm.append("---")

                parts = ["\n".join(fm), ""]
                if context:
                    # Operator note — embedded as data for ingest, never as instructions.
                    parts.append(f"> **Document Context** (provided at import): {context}")
                    parts.append("")
                parts += [f"# {title}", "", data["markdown"]]
                out = "\n".join(parts).rstrip() + "\n"

                target.parent.mkdir(parents=True, exist_ok=True)
                # Same-day re-import of the same deck overwrites (idempotent); the
                # file card's /dedupe-check warns the user before this point.
                target.write_text(out, encoding="utf-8")

                return {
                    "__status__": 200,
                    "result": f"✓ Imported {data['slides']} slides from {orig_filename}",
                    "kind": "pptx-import",
                    "output_file": None,
                    "created_files": [rel],
                    "is_error": False,
                    "duration_ms": int((time.time() - started) * 1000),
                }
        except Busy as busy:
            return {"__status__": 409, "error": "busy", "in_flight": busy.in_flight}

    def _handle_run(self) -> None:
        body = self._read_json_body()
        if body is None:
            return  # response already sent

        kind = body.get("kind")
        args = body.get("args") or {}
        if kind not in PROMPT_TEMPLATES:
            return _json_response(
                self,
                400,
                {"error": "bad_request", "detail": f"unknown kind: {kind!r}"},
            )
        if kind == "pdf-import":
            return _json_response(
                self,
                400,
                {
                    "error": "bad_request",
                    "detail": "pdf-import requires multipart upload — POST /upload-pdf",
                },
            )
        if kind == "file-import":
            return _json_response(
                self,
                400,
                {
                    "error": "bad_request",
                    "detail": "file-import requires multipart upload — POST /upload-file",
                },
            )

        prompt, err = _format_prompt(kind, args)
        if err is not None:
            return _json_response(self, 400, err)

        cfg = PROMPT_TEMPLATES[kind]
        envelope = self._run_kind(kind, prompt, cfg, args)
        status_code = envelope.pop("__status__", 200)
        _json_response(self, status_code, envelope)

    def _handle_open_folder(self) -> None:
        """Shell-open raw/ or wiki/ in Finder (macOS only, no skill exec)."""
        if sys.platform != "darwin":
            return _json_response(self, 200, {"ok": False, "detail": "not macOS"})
        body = self._read_json_body()
        if body is None:
            return
        folder = str(body.get("folder", "")).strip()
        allowed = {"raw": RAW_DIR, "wiki": WIKI_DIR}
        target = allowed.get(folder)
        if target is None:
            return _json_response(
                self, 400, {"error": "bad_request", "detail": "folder must be raw or wiki"}
            )
        try:
            subprocess.Popen(
                ["open", str(target)],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return _json_response(self, 500, {"ok": False, "detail": str(exc)})
        _json_response(self, 200, {"ok": True})

    def _handle_patch_finding(self) -> None:
        """Model-free: update an sb:finding status in a lint report in place.

        Body: { "filename": "YYYY-MM-DD_lint.md", "finding_id": "f1",
                "status": "applied" | "skipped" }
        """
        body = self._read_json_body()
        if body is None:
            return
        filename   = str(body.get("filename", "")).strip()
        finding_id = str(body.get("finding_id", "")).strip()
        new_status = str(body.get("status", "")).strip()

        if not filename or not finding_id or new_status not in ("applied", "skipped"):
            return _json_response(
                self, 400,
                {"error": "bad_request",
                 "detail": "filename, finding_id, and status (applied|skipped) are required"},
            )
        # Safe path: only files directly in outputs/, no subdirectories.
        if "/" in filename or "\\" in filename:
            return _json_response(self, 400, {"error": "bad_request", "detail": "invalid filename"})
        target = (OUTPUTS_DIR / filename).resolve()
        try:
            target.relative_to(OUTPUTS_DIR.resolve())
        except ValueError:
            return _json_response(self, 400, {"error": "bad_request", "detail": "invalid filename"})
        if not target.is_file():
            return _json_response(self, 404, {"error": "not_found"})

        import re as _re
        text = target.read_text(encoding="utf-8")

        # Patch the specific sb:finding tag's status attribute.
        old_tag = f'id="{finding_id}"'
        if old_tag not in text:
            return _json_response(self, 404, {"error": "not_found", "detail": f"finding {finding_id} not in report"})

        def _patch_finding_status(m: "_re.Match[str]") -> str:
            return m.group(0).replace('status="open"', f'status="{new_status}"')

        pattern = rf'<!--\s*sb:finding\s[^>]*?id="{_re.escape(finding_id)}"[^>]*?-->'
        text = _re.sub(pattern, _patch_finding_status, text)

        # Recalculate sb:delint totals from the patched text.
        open_count    = len(_re.findall(r'status="open"',    text))
        applied_count = len(_re.findall(r'status="applied"', text))
        skipped_count = len(_re.findall(r'status="skipped"', text))
        total_count   = open_count + applied_count + skipped_count

        def _patch_delint_totals(m: "_re.Match[str]") -> str:
            s = m.group(0)
            s = _re.sub(r'total="\d+"',   f'total="{total_count}"',   s)
            s = _re.sub(r'open="\d+"',    f'open="{open_count}"',     s)
            s = _re.sub(r'applied="\d+"', f'applied="{applied_count}"', s)
            s = _re.sub(r'skipped="\d+"', f'skipped="{skipped_count}"', s)
            return s

        text = _re.sub(r'<!--\s*sb:delint\s[^>]*?-->', _patch_delint_totals, text)

        target.write_text(text, encoding="utf-8")
        _json_response(self, 200, {
            "ok": True,
            "open": open_count, "applied": applied_count, "skipped": skipped_count,
        })

    def _handle_dedupe_check(self) -> None:
        """Model-free: does this import candidate already look imported?

        No long-op mutex, no skill exec — a pure filesystem scan of raw/.
        """
        body = self._read_json_body()
        if body is None:
            return  # response already sent
        try:
            result = _dedupe_check(body)
        except Exception as exc:  # never let a bad candidate 500 the check
            return _json_response(self, 200, {"matches": [], "error": str(exc)})
        _json_response(self, 200, result)

    def _run_kind(self, kind: str, prompt: str, cfg: dict, args: dict | None = None) -> dict:
        """Acquire the mutex, run the skill, and return the response envelope.

        The envelope includes a private `__status__` key the caller pops to
        send the right HTTP code (200/409/504/502).
        """
        args = args or {}

        before_outputs = _snapshot(OUTPUTS_DIR) if cfg.get("output_glob") else set()
        scoped_raw_dir = None
        if cfg.get("created_in_fn") and args:
            sub = cfg["created_in_fn"](args.get("file_path", ""))
            scoped_raw_dir = RAW_DIR if sub == "" else (RAW_DIR / sub)
        elif cfg.get("created_in") is not None:
            sub = cfg["created_in"]
            scoped_raw_dir = RAW_DIR if sub == "" else (RAW_DIR / sub)
        before_raw = _snapshot(scoped_raw_dir, "*") if scoped_raw_dir else set()

        try:
            with long_op(kind):
                status, result = run_skill(prompt, cfg)
        except Busy as busy:
            return {"__status__": 409, "error": "busy", "in_flight": busy.in_flight}

        if status != 200:
            return {"__status__": status, **result, "kind": kind}

        output_file = None
        if cfg.get("output_glob"):
            output_file = _newest_match(OUTPUTS_DIR, cfg["output_glob"], before_outputs)

        # If no new file was created but the skill's reply references an
        # existing outputs/<file>.md path (which happens when the same query
        # runs twice in a day and the skill short-circuits), surface that
        # path. If the reply doesn't even mention a path, fall back to a
        # slug-based lookup using the original arg (e.g. the question text).
        result_text = result.get("result", "") or ""
        if output_file is None and cfg.get("output_glob"):
            output_file = _extract_outputs_path(result_text)
        if output_file is None and cfg.get("fallback_finder"):
            try:
                output_file = cfg["fallback_finder"](args)
            except (KeyError, OSError):
                output_file = None

        created_files = []
        if scoped_raw_dir and scoped_raw_dir.exists():
            after = _snapshot(scoped_raw_dir, "*")
            created_files = sorted(
                str(p.relative_to(VAULT_ROOT)) for p in (after - before_raw)
            )

        # The skill's contract (second-brain-query Step 7, second-brain-lint
        # equivalent) is that the saved file IS the canonical record. The
        # in-conversation reply is supposed to mirror it, but the model
        # sometimes elides to an "Answer saved to: …" ack — and a previous
        # length-based heuristic guessed wrong on terse answers. Just always
        # prefer the file body when one resolves. The bridge is reading what
        # the skill already wrote; no synthesis.
        #
        # Thread kinds (thread-start, thread-reply) skip this: the frontend
        # fetches the file directly via GET /outputs/<filename> to render the
        # full chat view, so overwriting result with the raw file content is
        # wasteful and returns the entire thread instead of the new answer.
        if output_file and not cfg.get("skip_file_read"):
            file_body = _read_saved_output(output_file)
            if file_body:
                result = {**result, "result": file_body}

        return {
            "__status__": 200,
            **result,
            "kind": kind,
            "output_file": output_file,
            "created_files": created_files,
        }

    def _handle_set_model(self) -> None:
        """Session-persistent model tier selection (no bridge restart needed).

        Body: {"engine": "claude"|"codex", "tier": "sonnet"|"haiku"|…|"default"}
        "default" clears any explicit --model flag for subsequent skill runs.
        """
        global _CLAUDE_MODEL_TIER, _CODEX_MODEL_TIER

        body = self._read_json_body()
        if body is None:
            return

        engine = str(body.get("engine", AGENT_ENGINE)).strip().lower()
        if engine not in ("claude", "codex"):
            return _json_response(
                self, 400,
                {"error": "bad_request", "detail": "engine must be 'claude' or 'codex'"},
            )

        tier = str(body.get("tier", "default")).strip().lower()
        tier_map = _CLAUDE_TIER_MAP if engine == "claude" else _CODEX_TIER_MAP
        if tier != "default" and tier not in tier_map:
            return _json_response(
                self, 400,
                {"error": "bad_request",
                 "detail": f"unknown tier {tier!r}; valid: default, {', '.join(sorted(tier_map))}"},
            )

        if engine == "claude":
            _CLAUDE_MODEL_TIER = tier
        else:
            _CODEX_MODEL_TIER = tier

        _json_response(self, 200, {
            "ok":       True,
            "engine":   engine,
            "tier":     tier,
            "model_id": tier_map.get(tier),
        })

    # ----- helpers --------------------------------------------------------

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            _json_response(self, 400, {"error": "bad_request", "detail": "empty body"})
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _json_response(self, 400, {"error": "bad_request", "detail": f"invalid JSON: {exc}"})
            return None
        if not isinstance(data, dict):
            _json_response(self, 400, {"error": "bad_request", "detail": "body must be a JSON object"})
            return None
        return data

    def _serve_index(self) -> None:
        """Serve index.html with the per-startup token injected and CSP set.

        The token replaces a placeholder in a <meta> tag. Because this is the
        document origin, only same-origin scripts can read it back out of the
        DOM — a cross-origin page cannot, so the token cannot leak via a drive-
        by fetch.
        """
        index = DASHBOARD_DIR / "index.html"
        try:
            html = index.read_text(encoding="utf-8")
        except OSError:
            return self._not_found()
        html = html.replace(_TOKEN_PLACEHOLDER, BRIDGE_TOKEN)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, path: Path) -> None:
        if not path.is_file():
            return self._not_found()
        ctype, _ = mimetypes.guess_type(str(path))
        if ctype is None:
            ctype = "application/octet-stream"
        # Treat .js as a module-friendly mime; some old guesses return text/plain.
        if path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        if path.suffix in (".html", ".css"):
            ctype = f"{ctype}; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_text_file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._not_found()
        self.send_response(200)
        _cors_header(self)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_wiki_article(self, slug: str) -> None:
        if not _SAFE_FILENAME_RE.fullmatch(slug) or "/" in slug or "\\" in slug:
            return _json_response(self, 404, {"error": "not_found"})
        p = WIKI_DIR / f"{slug}.md"
        try:
            resolved = p.resolve()
            resolved.relative_to(WIKI_DIR.resolve())
        except ValueError:
            return _json_response(self, 404, {"error": "not_found"})
        if not resolved.is_file():
            return _json_response(self, 404, {"error": "not_found", "detail": f"no article: {slug}"})
        self._serve_text_file(resolved)

    def _serve_output_file(self, filename: str) -> None:
        if not _SAFE_FILENAME_RE.fullmatch(filename) or "/" in filename or "\\" in filename:
            return _json_response(self, 404, {"error": "not_found"})
        p = OUTPUTS_DIR / filename
        try:
            resolved = p.resolve()
            resolved.relative_to(OUTPUTS_DIR.resolve())
        except ValueError:
            return _json_response(self, 404, {"error": "not_found"})
        if not resolved.is_file():
            return _json_response(self, 404, {"error": "not_found", "detail": f"no output: {filename}"})
        self._serve_text_file(resolved)

    def _delete_output_file(self, filename: str) -> None:
        # Delete a single saved output. Same containment checks as the GET
        # path — refuse anything that isn't a plain filename directly inside
        # outputs/ so a crafted path can never reach outside the vault.
        if not _SAFE_FILENAME_RE.fullmatch(filename) or "/" in filename or "\\" in filename:
            return _json_response(self, 404, {"error": "not_found"})
        p = OUTPUTS_DIR / filename
        try:
            resolved = p.resolve()
            resolved.relative_to(OUTPUTS_DIR.resolve())
        except ValueError:
            return _json_response(self, 404, {"error": "not_found"})
        if not resolved.is_file():
            return _json_response(self, 404, {"error": "not_found", "detail": f"no output: {filename}"})
        try:
            resolved.unlink()
        except OSError as exc:
            return _json_response(self, 500, {"error": "delete_failed", "detail": str(exc)})
        return _json_response(self, 200, {"ok": True})

    def _serve_raw_file(self, subpath: str) -> None:
        # Serve a single markdown file from raw/ (read-only). Unlike wiki and
        # outputs, raw is nested (web/, pdf/, craft/, images/), so we validate
        # each path segment and rely on the resolved-path containment check.
        subpath = urllib.parse.unquote(subpath)
        if not subpath or "\\" in subpath or not subpath.endswith(".md"):
            return _json_response(self, 404, {"error": "not_found"})
        segments = subpath.split("/")
        for seg in segments:
            if seg in ("", ".", "..") or seg.startswith(".") or not _SAFE_FILENAME_RE.fullmatch(seg):
                return _json_response(self, 404, {"error": "not_found"})
        p = RAW_DIR / subpath
        try:
            resolved = p.resolve()
            resolved.relative_to(RAW_DIR.resolve())
        except ValueError:
            return _json_response(self, 404, {"error": "not_found"})
        if not resolved.is_file():
            return _json_response(self, 404, {"error": "not_found", "detail": f"no raw file: {subpath}"})
        self._serve_text_file(resolved)

    def _not_found(self) -> None:
        _json_response(self, 404, {"error": "not_found", "detail": self.path})


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def _clean_uploads() -> None:
    uploads = DASHBOARD_DIR / ".uploads"
    if not uploads.exists():
        return
    for p in uploads.iterdir():
        if p.name == ".gitignore":
            continue
        try:
            p.unlink()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Second Brain dashboard bridge")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind on 127.0.0.1 (default {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not auto-open the browser on startup",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind. Refuses anything other than localhost.",
    )
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost"):
        sys.stderr.write(
            f"refusing to bind to {args.host!r}: dashboard is localhost-only\n"
        )
        return 2

    # The dashboard is reachable on both localhost and 127.0.0.1; allow both as
    # request Origins regardless of which name the browser used to load it.
    global DASHBOARD_ORIGINS
    DASHBOARD_ORIGINS = frozenset(
        {f"http://localhost:{args.port}", f"http://127.0.0.1:{args.port}"}
    )

    _clean_uploads()

    server = http.server.ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}/"
    sys.stderr.write(f"Second Brain dashboard listening on {url}\n")
    sys.stderr.write(f"Vault: {VAULT_ROOT}\n")

    if not args.no_open and sys.platform == "darwin":
        try:
            subprocess.Popen(
                ["open", url],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nShutting down.\n")
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
