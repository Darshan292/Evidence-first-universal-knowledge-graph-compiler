"""Workspaces: one uploaded source, one isolated directory, one database.

    workspaces/<id>/
        source/        the extracted tree -- nothing is written outside it
        graph.sqlite   a FRESH database, never shared with another upload
        metadata.json  what was uploaded, what it hashed to, what happened

Two uploads never share a database. The compiler is append-only across corpus
revisions (K-1.2 §10), so merging unrelated sources would let one answer a
question about the other.

An uploaded archive is hostile input. Extraction refuses absolute paths, `..`
traversal, symlinks, unreasonable counts and unreasonable sizes -- and it refuses
them by *resolving* each destination and proving it stays inside the workspace,
which is the check that also defeats a symlinked parent directory.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kgc.safety import ANALYSED_SUFFIXES, LANGUAGE_BY_SUFFIX

MAX_ARCHIVE_BYTES = 200 * 1024 * 1024      # the uploaded .zip itself
MAX_TOTAL_BYTES = 400 * 1024 * 1024        # everything it expands to
MAX_FILES = 20_000
MAX_MEMBER_BYTES = 32 * 1024 * 1024


class UnsafeArchive(Exception):
    """The archive tried to write somewhere it must not, or is too large."""


@dataclass
class FileReport:
    """What happened to one uploaded file. An unsupported file is still a fact."""
    rel_path: str
    size: int
    sha256: str
    status: str                 # ACCEPTED | UNSUPPORTED | REJECTED
    reason: str = ""
    language: str | None = None


@dataclass
class Workspace:
    id: str
    root: Path
    name: str
    created: float = field(default_factory=time.time)

    @property
    def source(self) -> Path:
        return self.root / "source"

    @property
    def db(self) -> Path:
        return self.root / "graph.sqlite"

    @property
    def metadata_path(self) -> Path:
        return self.root / "metadata.json"

    def metadata(self) -> dict:
        if self.metadata_path.is_file():
            return json.loads(self.metadata_path.read_text())
        return {}

    def write_metadata(self, **fields) -> dict:
        meta = self.metadata()
        meta.update(fields)
        meta.setdefault("id", self.id)
        meta.setdefault("name", self.name)
        meta.setdefault("created", self.created)
        self.metadata_path.write_text(json.dumps(meta, indent=2))
        return meta


class WorkspaceStore:
    def __init__(self, base: str | Path):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)

    def create(self, name: str) -> Workspace:
        wid = uuid.uuid4().hex[:12]
        root = self.base / wid
        (root / "source").mkdir(parents=True)
        ws = Workspace(wid, root, name or "untitled")
        ws.write_metadata(status="CREATED", files=[])
        return ws

    def get(self, wid: str) -> Workspace | None:
        # the id is used as a path component; accept only what we generate
        if not wid or not wid.isalnum() or len(wid) > 32:
            return None
        root = self.base / wid
        if not (root / "metadata.json").is_file():
            return None
        meta = json.loads((root / "metadata.json").read_text())
        return Workspace(wid, root, meta.get("name", wid), meta.get("created", 0))

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.base.iterdir(), reverse=True):
            if (p / "metadata.json").is_file():
                meta = json.loads((p / "metadata.json").read_text())
                out.append({k: meta.get(k) for k in
                            ("id", "name", "created", "status", "counts", "compile")})
        return out

    def delete(self, wid: str) -> bool:
        ws = self.get(wid)
        if ws is None:
            return False
        shutil.rmtree(ws.root, ignore_errors=True)
        return True


# ── safe extraction ─────────────────────────────────────────────────────

def _safe_destination(dest_root: Path, member_name: str) -> Path:
    """Resolve where a member wants to land and prove it stays inside.

    Rejects absolute paths, drive letters, `..` traversal and anything that
    resolves outside the workspace -- including through a symlink already
    written by an earlier member of the same archive.
    """
    name = member_name.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise UnsafeArchive(f"absolute path in archive: {member_name!r}")
    if any(part == ".." for part in name.split("/")):
        raise UnsafeArchive(f"path traversal in archive: {member_name!r}")
    target = (dest_root / name).resolve()
    root = dest_root.resolve()
    if target != root and root not in target.parents:
        raise UnsafeArchive(f"archive member escapes the workspace: {member_name!r}")
    return target


def extract_zip(data: bytes, dest_root: Path) -> list[FileReport]:
    """Extract an uploaded archive into a workspace. Every refusal is reported."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise UnsafeArchive(f"archive is {len(data)} bytes, over the "
                            f"{MAX_ARCHIVE_BYTES} limit")
    dest_root.mkdir(parents=True, exist_ok=True)
    reports: list[FileReport] = []
    import io
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UnsafeArchive(f"not a readable zip archive: {e}") from None

    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > MAX_FILES:
            raise UnsafeArchive(f"{len(members)} files, over the {MAX_FILES} limit")
        total = sum(m.file_size for m in members)
        if total > MAX_TOTAL_BYTES:
            raise UnsafeArchive(f"expands to {total} bytes, over the "
                                f"{MAX_TOTAL_BYTES} limit")
        for m in members:
            # 0xA000 marks a symlink in the external attributes of a unix zip
            if (m.external_attr >> 16) & 0xA000 == 0xA000:
                reports.append(FileReport(m.filename, m.file_size, "", "REJECTED",
                                          "symlink members are not extracted"))
                continue
            if m.file_size > MAX_MEMBER_BYTES:
                reports.append(FileReport(m.filename, m.file_size, "", "REJECTED",
                                          f"member exceeds {MAX_MEMBER_BYTES} bytes"))
                continue
            try:
                target = _safe_destination(dest_root, m.filename)
            except UnsafeArchive as e:
                reports.append(FileReport(m.filename, m.file_size, "", "REJECTED", str(e)))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src:
                body = src.read(MAX_MEMBER_BYTES + 1)
            if len(body) > MAX_MEMBER_BYTES:
                reports.append(FileReport(m.filename, len(body), "", "REJECTED",
                                          "declared size understated the real size"))
                continue
            target.write_bytes(body)
            reports.append(classify_upload(m.filename, body))
    return reports


def classify_upload(rel_path: str, body: bytes) -> FileReport:
    """Say plainly what will happen to a file. Never fabricate a graph from one."""
    suffix = Path(rel_path).suffix.lower()
    digest = hashlib.sha256(body).hexdigest()
    if suffix in ANALYSED_SUFFIXES:
        return FileReport(rel_path, len(body), digest, "ACCEPTED",
                          "an analyser exists for this format",
                          LANGUAGE_BY_SUFFIX.get(suffix))
    return FileReport(rel_path, len(body), digest, "UNSUPPORTED",
                      f"no analyser exists for {suffix or 'this file'}; it is recorded "
                      f"but contributes no symbols or claims",
                      LANGUAGE_BY_SUFFIX.get(suffix))


def write_single_file(name: str, body: bytes, dest_root: Path) -> FileReport:
    """Accept one uploaded file, with the same destination checks as an archive."""
    dest_root.mkdir(parents=True, exist_ok=True)
    target = _safe_destination(dest_root, Path(name).name)
    target.write_bytes(body)
    return classify_upload(Path(name).name, body)


def report_dicts(reports: list[FileReport]) -> list[dict]:
    return [asdict(r) for r in reports]
