"""Content-addressed per-turn workspace checkpoint store and rollback engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from backend.app.shared.errors import DomainError, InvalidWorkspaceError, PathEscapeError

_IGNORED_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        "dist",
        ".abyss-data",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)
_MAX_SNAPSHOT_FILE_BYTES = 2 * 1024 * 1024
_MAX_SCANNED_FILES = 5_000


class BaselineEntry(TypedDict, total=False):
    existed: bool
    sha256: str
    mode: int


class TurnCheckpointManifest(TypedDict):
    checkpoint_id: str
    session_id: str
    created_at: str
    changed_files: list[str]
    baseline_files: dict[str, BaselineEntry]


def _validate_safe_id(raw_id: str) -> str:
    clean = raw_id.strip()
    if not clean or "/" in clean or "\\" in clean or clean in {".", ".."}:
        raise PathEscapeError(raw_id)
    return clean


def _resolve_confined(root: Path, rel_path: str) -> Path:
    clean = rel_path.strip().lstrip("/")
    if not clean:
        raise PathEscapeError(rel_path)
    target = (root / clean).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PathEscapeError(rel_path) from exc
    if target == root:
        raise PathEscapeError(rel_path)
    return target


@dataclass
class CheckpointCapture:
    """In-flight pre-turn snapshot capture bound on RunCatalog for one agent run."""

    store: TurnCheckpointStore
    session_id: str
    workspace_path: Path
    _baseline_hashes: dict[str, tuple[str, int]] = field(default_factory=dict)
    _extra_pre_write: dict[str, BaselineEntry] = field(default_factory=dict)
    _captured: bool = False
    checkpoint_id: str | None = None
    changed_files: list[str] = field(default_factory=list)

    async def capture_baseline(self) -> None:
        if self._captured:
            return
        self._baseline_hashes = await asyncio.to_thread(
            self.store.scan_and_store_blobs,
            self.session_id,
            self.workspace_path,
        )
        self._captured = True

    async def snapshot_before_write(self, virtual_or_rel_path: str) -> None:
        clean = virtual_or_rel_path.strip().lstrip("/")
        if not clean or clean.startswith(("artifacts/", "memories/", "skills/")):
            return
        if clean in self._baseline_hashes or clean in self._extra_pre_write:
            return
        entry = await asyncio.to_thread(
            self.store.snapshot_single_file,
            self.session_id,
            self.workspace_path,
            clean,
        )
        if entry is not None:
            self._extra_pre_write[clean] = entry

    async def finalize(self) -> tuple[str | None, list[str]]:
        if not self._captured and not self._extra_pre_write:
            return None, []
        cid, changed = await asyncio.to_thread(
            self.store.finalize_turn_sync,
            self.session_id,
            self.workspace_path,
            self._baseline_hashes,
            self._extra_pre_write,
        )
        self.checkpoint_id = cid
        self.changed_files = changed
        return cid, changed


class TurnCheckpointStore:
    """Persists content-addressed pre-turn snapshots and restores workspace state on revert."""

    def __init__(self, checkpoints_root: Path) -> None:
        self._root = checkpoints_root.resolve()

    def session_dir(self, session_id: str) -> Path:
        safe = _validate_safe_id(session_id)
        target = (self._root / safe).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise PathEscapeError(session_id) from exc
        return target

    def delete_session_checkpoints(self, session_id: str) -> None:
        sdir = self.session_dir(session_id)
        if sdir.exists():
            shutil.rmtree(sdir, ignore_errors=True)

    def new_capture(self, session_id: str, workspace_path: Path) -> CheckpointCapture:
        return CheckpointCapture(
            store=self,
            session_id=session_id,
            workspace_path=workspace_path.resolve(),
        )

    def _write_blob(self, blobs_dir: Path, raw: bytes) -> str:
        digest = hashlib.sha256(raw).hexdigest()
        blob_path = blobs_dir / digest
        if not blob_path.exists():
            blobs_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = blobs_dir / f".{digest}.{uuid.uuid4().hex[:8]}.tmp"
            tmp_path.write_bytes(raw)
            os.replace(tmp_path, blob_path)
        return digest

    def snapshot_single_file(
        self,
        session_id: str,
        workspace_path: Path,
        rel_path: str,
    ) -> BaselineEntry | None:
        root = workspace_path.resolve()
        try:
            target = _resolve_confined(root, rel_path)
        except PathEscapeError:
            return None
        norm_rel = target.relative_to(root).as_posix()
        if any(part in _IGNORED_NAMES for part in Path(norm_rel).parts):
            return None
        if not target.exists():
            return BaselineEntry(existed=False)
        if target.is_symlink() or not target.is_file():
            return None
        try:
            st = target.stat()
            if st.st_size > _MAX_SNAPSHOT_FILE_BYTES:
                return None
            raw = target.read_bytes()
        except OSError:
            return None
        blobs_dir = self.session_dir(session_id) / "blobs"
        digest = self._write_blob(blobs_dir, raw)
        return BaselineEntry(
            existed=True,
            sha256=digest,
            mode=stat.S_IMODE(st.st_mode),
        )

    def scan_and_store_blobs(
        self,
        session_id: str,
        workspace_path: Path,
    ) -> dict[str, tuple[str, int]]:
        root = workspace_path.resolve()
        if not root.is_dir():
            return {}
        blobs_dir = self.session_dir(session_id) / "blobs"
        manifest: dict[str, tuple[str, int]] = {}
        scanned = 0

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d not in _IGNORED_NAMES and not os.path.islink(os.path.join(dirpath, d))
            )
            for fname in sorted(filenames):
                if fname in _IGNORED_NAMES:
                    continue
                if scanned >= _MAX_SCANNED_FILES:
                    return manifest
                full = Path(dirpath) / fname
                if full.is_symlink():
                    continue
                try:
                    st = full.stat()
                    if not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_SNAPSHOT_FILE_BYTES:
                        continue
                    raw = full.read_bytes()
                except OSError:
                    continue
                rel = full.relative_to(root).as_posix()
                digest = self._write_blob(blobs_dir, raw)
                manifest[rel] = (digest, stat.S_IMODE(st.st_mode))
                scanned += 1
        return manifest

    def _scan_hashes_only(self, workspace_path: Path) -> dict[str, str]:
        root = workspace_path.resolve()
        if not root.is_dir():
            return {}
        hashes: dict[str, str] = {}
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d not in _IGNORED_NAMES and not os.path.islink(os.path.join(dirpath, d))
            )
            for fname in sorted(filenames):
                if fname in _IGNORED_NAMES:
                    continue
                if scanned >= _MAX_SCANNED_FILES:
                    return hashes
                full = Path(dirpath) / fname
                if full.is_symlink():
                    continue
                try:
                    st = full.stat()
                    if not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_SNAPSHOT_FILE_BYTES:
                        continue
                    raw = full.read_bytes()
                except OSError:
                    continue
                rel = full.relative_to(root).as_posix()
                hashes[rel] = hashlib.sha256(raw).hexdigest()
                scanned += 1
        return hashes

    def finalize_turn_sync(
        self,
        session_id: str,
        workspace_path: Path,
        baseline_hashes: dict[str, tuple[str, int]],
        extra_pre_write: dict[str, BaselineEntry],
    ) -> tuple[str | None, list[str]]:
        root = workspace_path.resolve()
        current_hashes = self._scan_hashes_only(root)

        candidate_paths = set(baseline_hashes.keys()) | set(current_hashes.keys()) | set(
            extra_pre_write.keys()
        )
        baseline_files: dict[str, BaselineEntry] = {}

        for rel in sorted(candidate_paths):
            if rel in baseline_hashes:
                old_sha, old_mode = baseline_hashes[rel]
                cur_sha = current_hashes.get(rel)
                if cur_sha != old_sha:
                    baseline_files[rel] = BaselineEntry(
                        existed=True,
                        sha256=old_sha,
                        mode=old_mode,
                    )
            elif rel in extra_pre_write:
                entry = extra_pre_write[rel]
                target = root / rel
                if entry.get("existed"):
                    old_sha = entry.get("sha256", "")
                    cur_sha = (
                        hashlib.sha256(target.read_bytes()).hexdigest()
                        if target.is_file() and not target.is_symlink()
                        else None
                    )
                    if cur_sha != old_sha:
                        baseline_files[rel] = entry
                else:
                    if target.exists():
                        baseline_files[rel] = BaselineEntry(existed=False)
            else:
                # Newly created in workspace during this turn
                if rel in current_hashes:
                    baseline_files[rel] = BaselineEntry(existed=False)

        if not baseline_files:
            return None, []

        checkpoint_id = uuid.uuid4().hex[:12]
        changed_files = sorted(baseline_files.keys())
        manifest = TurnCheckpointManifest(
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            created_at=datetime.now(UTC).isoformat(),
            changed_files=changed_files,
            baseline_files=baseline_files,
        )
        turns_dir = self.session_dir(session_id) / "turns"
        turns_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = turns_dir / f"{checkpoint_id}.json"
        tmp_path = turns_dir / f".{checkpoint_id}.tmp"
        tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, manifest_path)
        return checkpoint_id, changed_files

    async def revert_checkpoints(
        self,
        session_id: str,
        workspace_path: Path,
        checkpoint_ids_newest_first: list[str],
    ) -> list[str]:
        return await asyncio.to_thread(
            self._revert_checkpoints_sync,
            session_id,
            workspace_path,
            checkpoint_ids_newest_first,
        )

    def _revert_checkpoints_sync(
        self,
        session_id: str,
        workspace_path: Path,
        checkpoint_ids_newest_first: list[str],
    ) -> list[str]:
        root = workspace_path.resolve()
        if not root.is_dir():
            raise InvalidWorkspaceError(str(workspace_path))

        sdir = self.session_dir(session_id)
        turns_dir = sdir / "turns"
        blobs_dir = sdir / "blobs"
        restored_paths: set[str] = set()

        for cid in checkpoint_ids_newest_first:
            safe_cid = _validate_safe_id(cid)
            manifest_path = turns_dir / f"{safe_cid}.json"
            if not manifest_path.is_file():
                continue
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            baseline_files = raw_manifest.get("baseline_files") or {}

            for rel_path, entry in baseline_files.items():
                if not isinstance(rel_path, str) or not isinstance(entry, dict):
                    continue
                target = _resolve_confined(root, rel_path)
                if entry.get("existed"):
                    sha = str(entry.get("sha256") or "")
                    safe_sha = _validate_safe_id(sha)
                    blob_path = blobs_dir / safe_sha
                    if not blob_path.is_file():
                        raise DomainError(
                            f"Checkpoint blob missing for '{rel_path}'.",
                            status_code=409,
                            code="CHECKPOINT_BLOB_MISSING",
                        )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp_target = target.parent / f".{target.name}.{uuid.uuid4().hex[:6]}.revert"
                    tmp_target.write_bytes(blob_path.read_bytes())
                    mode_val = entry.get("mode")
                    if isinstance(mode_val, int) and mode_val > 0:
                        os.chmod(tmp_target, mode_val)
                    os.replace(tmp_target, target)
                else:
                    if target.is_file() or target.is_symlink():
                        target.unlink()
                    self._prune_empty_parents(root, target.parent)
                restored_paths.add(rel_path)

            manifest_path.unlink(missing_ok=True)

        return sorted(restored_paths)

    @staticmethod
    def _prune_empty_parents(root: Path, start: Path) -> None:
        cur = start
        while cur != root:
            try:
                cur.relative_to(root)
            except ValueError:
                break
            try:
                cur.rmdir()
            except OSError:
                break
            cur = cur.parent
