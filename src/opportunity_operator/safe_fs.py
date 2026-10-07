"""The ONLY place the application touches the filesystem.

`DataDir` confines every read/write to one directory. Any path that is absolute, contains
`..`, passes through a symlink, or resolves outside the root is refused. The data directory
itself must not live inside a git working tree, so project repositories can never be the
storage location, and optional `forbidden_roots` can name additional locations to refuse.

An AST test (tests/isolation/test_no_filesystem_surface.py) enforces that no other module in
the package opens files, lists directories, spawns processes, or imports repository tooling.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from .errors import PathEscapeError, UnsafeDataDir


def _inside_git_worktree(path: Path) -> bool:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return True
    return False


class DataDir:
    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        create: bool = True,
        forbidden_roots: Iterable[str | os.PathLike[str]] = (),
    ) -> None:
        raw = Path(root).expanduser()
        if raw.is_symlink():
            raise UnsafeDataDir("data directory must not be a symlink")
        if create:
            raw.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved = raw.resolve(strict=True)
        if not resolved.is_dir():
            raise UnsafeDataDir("data directory is not a directory")
        if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
            raise UnsafeDataDir("data directory must be a dedicated directory")
        if _inside_git_worktree(resolved):
            raise UnsafeDataDir("data directory must not be inside a git working tree")
        for forbidden in forbidden_roots:
            fr = Path(forbidden).expanduser().resolve()
            if resolved == fr or fr in resolved.parents or resolved in fr.parents:
                raise UnsafeDataDir("data directory overlaps a forbidden root")
        os.chmod(resolved, 0o700)
        self.root = resolved

    # ---- path confinement -------------------------------------------------
    def resolve(self, rel: str | os.PathLike[str]) -> Path:
        rel_path = Path(rel)
        if rel_path.is_absolute() or "\x00" in str(rel):
            raise PathEscapeError("absolute or malformed path refused")
        if ".." in rel_path.parts:
            raise PathEscapeError("parent traversal refused")
        current = self.root
        for part in rel_path.parts:
            current = current / part
            if current.is_symlink():
                raise PathEscapeError("symlink in path refused")
        resolved = current.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise PathEscapeError("path resolves outside the data directory")
        return resolved

    # ---- IO ----------------------------------------------------------------
    def exists(self, rel: str) -> bool:
        return self.resolve(rel).exists()

    def ensure_dir(self, rel: str) -> Path:
        p = self.resolve(rel)
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        return p

    def get_bytes(self, rel: str) -> bytes:
        return self.resolve(rel).read_bytes()

    def get_text(self, rel: str) -> str:
        return self.resolve(rel).read_text(encoding="utf-8")

    def put_bytes(self, rel: str, data: bytes) -> Path:
        target = self.resolve(rel)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return target

    def put_text(self, rel: str, text: str) -> Path:
        return self.put_bytes(rel, text.encode("utf-8"))

    def db_path(self, rel: str = "operator.db") -> str:
        return str(self.resolve(rel))

    def walk_files(self) -> Iterator[Path]:
        """Every file under the data directory (used by the leakage sweep)."""
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                yield Path(dirpath) / name
