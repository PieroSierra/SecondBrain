#!/usr/bin/env python3
"""Deterministic raw-file change detection for Second Brain ingestion.

The dashboard bridge imports this module directly.  The ingest skill also uses
the tiny ``prepare`` / ``finalize`` CLI below when it is invoked interactively
without the bridge.  Keeping both paths here prevents the model prompt from
having to reimplement manifest or filesystem comparison logic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:  # POSIX (the Swift app is macOS); keep imports usable on other platforms.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX systems
    fcntl = None


FINGERPRINT_KEY = "fingerprint"
PLAN_VERSION = 1
HASH_CHUNK_BYTES = 1024 * 1024
INCOMPLETE_MARKER = b"<!-- sb:incomplete -->"

TEXT_SUFFIXES = frozenset({".md", ".txt"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | IMAGE_SUFFIXES | {".pdf"}

_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_HASH_CACHE_LIMIT = 4096


class IngestStateError(RuntimeError):
    """Base error for deterministic ingestion-state operations."""


class SourceChanged(IngestStateError):
    """Raised when a source changes while its fingerprint is being captured."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _mtime_iso(mtime_ns: int) -> str:
    return (
        datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _valid_iso(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _valid_entry(entry: object) -> bool:
    return isinstance(entry, dict) and _valid_iso(entry.get("ingested_at"))


def _valid_fingerprint(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    mtime_ns = value.get("mtime_ns")
    size = value.get("size")
    sha256 = value.get("sha256")
    return (
        isinstance(mtime_ns, int)
        and not isinstance(mtime_ns, bool)
        and mtime_ns >= 0
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size >= 0
        and isinstance(sha256, str)
        and len(sha256) == 64
        and all(c in "0123456789abcdef" for c in sha256)
    )


def load_manifest(vault_root: Path) -> tuple[dict, bool]:
    """Return ``(manifest, valid_json_object)`` without raising on corruption."""

    path = vault_root / "raw" / ".ingest-manifest.json"
    if not path.exists():
        return {}, False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}, False
    return (value, True) if isinstance(value, dict) else ({}, False)


def raw_user_files(vault_root: Path) -> dict[str, Path]:
    """Return the same user-facing raw-file universe used by status and ingest."""

    raw_dir = vault_root / "raw"
    out: dict[str, Path] = {}
    if not raw_dir.exists():
        return out
    for path in raw_dir.rglob("*"):
        if not path.is_file():
            continue
        raw_rel = path.relative_to(raw_dir)
        if any(part.startswith(".") for part in raw_rel.parts):
            continue
        rel = path.relative_to(vault_root).as_posix()
        if ".assets/" in rel or rel.endswith(".assets"):
            continue
        out[rel] = path
    return out


def fingerprint_file(path: Path, *, use_cache: bool = True) -> dict:
    """Stream a stable fingerprint, retrying once if the source changes mid-read."""

    last_error: OSError | None = None
    for _attempt in range(2):
        try:
            before = path.stat()
            key = (str(path), before.st_mtime_ns, before.st_size)
            cached = _HASH_CACHE.get(key) if use_cache else None
            if cached is None:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    while chunk := handle.read(HASH_CHUNK_BYTES):
                        digest.update(chunk)
                sha256 = digest.hexdigest()
            else:
                sha256 = cached
            after = path.stat()
        except OSError as exc:
            last_error = exc
            break

        if (
            before.st_mtime_ns == after.st_mtime_ns
            and before.st_size == after.st_size
        ):
            if use_cache:
                if len(_HASH_CACHE) >= _HASH_CACHE_LIMIT:
                    _HASH_CACHE.clear()
                _HASH_CACHE[key] = sha256
            return {
                "mtime_ns": after.st_mtime_ns,
                "size": after.st_size,
                "sha256": sha256,
            }

    if last_error is not None:
        raise last_error
    raise SourceChanged(f"source changed while hashing: {path}")


def _is_incomplete_pdf_markdown(rel: str, path: Path) -> bool:
    if not rel.startswith("raw/pdf/") or path.suffix.lower() != ".md":
        return False
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            handle.seek(max(0, size - 64 * 1024))
            return INCOMPLETE_MARKER in handle.read()
    except OSError:
        return False


def _classification(
    rel: str,
    path: Path,
    entry: object,
    *,
    baseline_legacy: bool,
) -> dict:
    suffix = path.suffix.lower()
    try:
        stat = path.stat()
    except OSError as exc:
        return {
            "path": rel,
            "state": "unreadable",
            "pending": True,
            "processable": False,
            "error": str(exc),
        }

    if entry is None:
        state = "new"
        needs_hash = True
    elif not _valid_entry(entry):
        state = "invalid"
        needs_hash = True
    else:
        stored = entry.get(FINGERPRINT_KEY)
        if stored is None:
            state = "legacy_baseline" if baseline_legacy else "legacy"
            needs_hash = True
        elif not _valid_fingerprint(stored):
            state = "invalid"
            needs_hash = True
        elif stored["mtime_ns"] == stat.st_mtime_ns and stored["size"] == stat.st_size:
            return {
                "path": rel,
                "state": "current",
                "pending": False,
                "processable": False,
                "fingerprint": dict(stored),
            }
        else:
            state = "compare_content"
            needs_hash = True

    fingerprint = None
    if needs_hash:
        try:
            fingerprint = fingerprint_file(path)
        except (OSError, SourceChanged) as exc:
            return {
                "path": rel,
                "state": "unreadable" if isinstance(exc, OSError) else "changed_during_scan",
                "pending": True,
                "processable": False,
                "error": str(exc),
            }

    if state == "compare_content":
        stored = entry[FINGERPRINT_KEY]
        state = "metadata_only" if fingerprint["sha256"] == stored["sha256"] else "changed"

    pending = state in {"new", "invalid", "legacy", "changed"}
    processable = pending and suffix in SUPPORTED_SUFFIXES

    if _is_incomplete_pdf_markdown(rel, path):
        state = "incomplete"
        pending = True
        processable = False
    elif pending and suffix not in SUPPORTED_SUFFIXES:
        state = "unsupported"
        processable = False

    return {
        "path": rel,
        "state": state,
        "pending": pending,
        "processable": processable,
        "fingerprint": fingerprint,
    }


def scan_vault(
    vault_root: Path,
    *,
    manifest: dict | None = None,
    manifest_ok: bool | None = None,
    files: dict[str, Path] | None = None,
    baseline_legacy: bool = False,
) -> dict:
    """Classify all live raw files without changing the manifest."""

    if manifest is None or manifest_ok is None:
        manifest, manifest_ok = load_manifest(vault_root)
    files = files if files is not None else raw_user_files(vault_root)
    items: list[dict] = []
    for rel, path in sorted(files.items()):
        entry = manifest.get(rel) if manifest_ok else None
        items.append(
            _classification(rel, path, entry, baseline_legacy=baseline_legacy)
        )
    return {
        "items": items,
        "pending_count": sum(1 for item in items if item["pending"]),
        "processable_count": sum(1 for item in items if item["processable"]),
        "total_count": len(items),
    }


@contextmanager
def _manifest_lock(vault_root: Path) -> Iterator[None]:
    raw_dir = vault_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    lock_path = raw_dir / ".ingest-manifest.lock"
    with lock_path.open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _apply_safe_fingerprint_updates(manifest: dict, scan: dict) -> int:
    updates = 0
    for item in scan["items"]:
        if item["state"] not in {"legacy_baseline", "metadata_only"}:
            continue
        entry = manifest.get(item["path"])
        if not _valid_entry(entry) or not _valid_fingerprint(item.get("fingerprint")):
            continue
        new_entry = dict(entry)
        new_entry[FINGERPRINT_KEY] = dict(item["fingerprint"])
        manifest[item["path"]] = new_entry
        updates += 1
    return updates


def migrate_manifest(vault_root: Path) -> dict:
    """Silently baseline legacy entries and normalize metadata-only changes."""

    with _manifest_lock(vault_root):
        manifest, manifest_ok = load_manifest(vault_root)
        if not manifest_ok:
            return {"updated": 0, "manifest_ok": False}
        scan = scan_vault(
            vault_root,
            manifest=manifest,
            manifest_ok=True,
            baseline_legacy=True,
        )
        updated = _apply_safe_fingerprint_updates(manifest, scan)
        if updated:
            _write_json_atomic(vault_root / "raw" / ".ingest-manifest.json", manifest)
        return {"updated": updated, "manifest_ok": True, **scan}


def _plan_process_paths(items: list[dict], files: dict[str, Path]) -> list[str]:
    process_paths: set[str] = set()
    markdown_by_dir: dict[Path, list[str]] = {}
    for rel, path in files.items():
        if path.suffix.lower() == ".md":
            markdown_by_dir.setdefault(path.parent, []).append(rel)

    for item in items:
        if not item["processable"]:
            continue
        rel = item["path"]
        path = files[rel]
        if path.suffix.lower() in IMAGE_SUFFIXES:
            process_paths.update(markdown_by_dir.get(path.parent, []))
        else:
            process_paths.add(rel)
    return sorted(process_paths)


def _cleanup_old_plans(state_dir: Path) -> None:
    cutoff = time.time() - 24 * 60 * 60
    if not state_dir.exists():
        return
    for path in state_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def prepare_scan(vault_root: Path, *, plan_path: Path | None = None) -> dict:
    """Normalize safe metadata changes and persist a model-readable scan plan."""

    state_dir = vault_root / "dashboard" / ".ingest-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_old_plans(state_dir)

    with _manifest_lock(vault_root):
        manifest, manifest_ok = load_manifest(vault_root)
        files = raw_user_files(vault_root)
        scan = scan_vault(
            vault_root,
            manifest=manifest,
            manifest_ok=manifest_ok,
            files=files,
            baseline_legacy=manifest_ok,
        )
        updated = 0
        if manifest_ok:
            updated = _apply_safe_fingerprint_updates(manifest, scan)
            if updated:
                _write_json_atomic(vault_root / "raw" / ".ingest-manifest.json", manifest)
                scan = scan_vault(
                    vault_root,
                    manifest=manifest,
                    manifest_ok=True,
                    files=files,
                    baseline_legacy=False,
                )

        plan_id = uuid.uuid4().hex
        if plan_path is None:
            plan_path = state_dir / f"{plan_id}.json"
        pending_items = [item for item in scan["items"] if item["pending"]]
        plan = {
            "version": PLAN_VERSION,
            "scan_id": plan_id,
            "created_at": _now_iso(),
            "manifest_ok": manifest_ok,
            "baseline_entries_added": updated,
            "pending_items": pending_items,
            "process_paths": _plan_process_paths(pending_items, files),
        }
        _write_json_atomic(plan_path, plan)
    return {"plan": plan, "plan_path": plan_path}


def finalize_plan(vault_root: Path, plan_path: Path) -> dict:
    """Record successfully processed plan items that have not changed since scan."""

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IngestStateError(f"cannot read ingest scan plan: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("version") != PLAN_VERSION:
        raise IngestStateError("unsupported or malformed ingest scan plan")

    process_paths = set(plan.get("process_paths") or [])
    planned = {
        item.get("path"): item
        for item in plan.get("pending_items") or []
        if isinstance(item, dict) and item.get("processable")
    }
    finalized: list[str] = []
    changed_during_ingest: list[str] = []

    with _manifest_lock(vault_root):
        manifest, manifest_ok = load_manifest(vault_root)
        if not manifest_ok:
            manifest = {}
        now = _now_iso()
        for rel, item in planned.items():
            # Images are finalized when their associated markdown was part of
            # the process set; all other processable items name themselves.
            path = vault_root / rel
            if path.suffix.lower() in IMAGE_SUFFIXES:
                siblings = {
                    candidate.relative_to(vault_root).as_posix()
                    for candidate in path.parent.glob("*.md")
                    if candidate.is_file()
                }
                was_processed = bool(siblings & process_paths)
            else:
                was_processed = rel in process_paths
            if not was_processed:
                continue
            expected = item.get("fingerprint")
            if not _valid_fingerprint(expected):
                continue
            try:
                current = fingerprint_file(path, use_cache=False)
            except (OSError, SourceChanged):
                changed_during_ingest.append(rel)
                continue
            if current != expected:
                changed_during_ingest.append(rel)
                continue
            old = manifest.get(rel)
            entry = dict(old) if isinstance(old, dict) else {}
            entry.update({
                "last_modified": _mtime_iso(current["mtime_ns"]),
                "ingested_at": now,
                FINGERPRINT_KEY: current,
            })
            manifest[rel] = entry
            finalized.append(rel)
        if finalized:
            _write_json_atomic(vault_root / "raw" / ".ingest-manifest.json", manifest)

    try:
        plan_path.unlink()
    except OSError:
        pass
    return {
        "scan_id": plan.get("scan_id"),
        "finalized": finalized,
        "changed_during_ingest": changed_during_ingest,
    }


def discard_plan(plan_path: Path) -> None:
    try:
        plan_path.unlink()
    except OSError:
        pass


def _default_vault_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Second Brain ingestion-state helper")
    parser.add_argument("command", choices=("prepare", "finalize"))
    args = parser.parse_args(argv)
    vault_root = _default_vault_root()
    direct_plan = vault_root / "dashboard" / ".ingest-state" / "direct.json"

    if args.command == "prepare":
        result = prepare_scan(vault_root, plan_path=direct_plan)
        plan = result["plan"]
        print(json.dumps({
            "scan_id": plan["scan_id"],
            "scan_plan": str(direct_plan.relative_to(vault_root)),
            "pending": len(plan["pending_items"]),
            "processable": len(plan["process_paths"]),
            "baseline_entries_added": plan["baseline_entries_added"],
        }))
        return 0

    result = finalize_plan(vault_root, direct_plan)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
