"""Records interpreter-level audit events (file opens, directory listings, sockets, processes).

Used to *demonstrate* that a pipeline run never touched forbidden locations, rather than
assuming it from reading the code.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

WATCHED = {
    "open", "os.listdir", "os.scandir", "os.walk", "glob.glob", "shutil.copyfile", "shutil.copytree",
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "socket.connect",
    "socket.getaddrinfo", "os.remove", "os.rename",
}
_events: list[tuple[str, tuple[Any, ...]]] = []
_active = False
_installed = False


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if _active and event in WATCHED:
        _events.append((event, args))


def _install() -> None:
    global _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True


@contextmanager
def record() -> Iterator[list[tuple[str, tuple[Any, ...]]]]:
    global _active
    _install()
    _events.clear()
    _active = True
    try:
        yield _events
    finally:
        _active = False


def paths_touched(events: list[tuple[str, tuple[Any, ...]]]) -> list[str]:
    out: list[str] = []
    for ev, args in events:
        if ev in {"open", "os.listdir", "os.scandir", "os.remove", "os.rename"} and args:
            first = args[0]
            if isinstance(first, bytes):
                first = first.decode(errors="replace")
            if isinstance(first, str):
                out.append(first)
    return out
