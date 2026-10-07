from __future__ import annotations

import os

import pytest

from opportunity_operator.errors import PathEscapeError, UnsafeDataDir
from opportunity_operator.safe_fs import DataDir


def test_basic_roundtrip_and_permissions(tmp_path):
    dd = DataDir(tmp_path / "d")
    dd.put_text("a/b.txt", "hi")
    assert dd.get_text("a/b.txt") == "hi"
    assert (dd.root.stat().st_mode & 0o777) == 0o700
    assert (dd.resolve("a/b.txt").stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("bad", ["../x", "a/../../x", "/etc/passwd", "a/\x00b", "..", "a/../.."])
def test_traversal_and_absolute_paths_refused(tmp_path, bad):
    dd = DataDir(tmp_path / "d")
    with pytest.raises(PathEscapeError):
        dd.get_text(bad)
    with pytest.raises(PathEscapeError):
        dd.put_text(bad, "x")


def test_symlink_pointing_outside_is_refused_for_read_and_write(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET")
    dd = DataDir(tmp_path / "d")
    os.symlink(outside, dd.root / "link")
    with pytest.raises(PathEscapeError):
        dd.get_text("link/secret.txt")
    with pytest.raises(PathEscapeError):
        dd.put_text("link/new.txt", "x")
    assert not (outside / "new.txt").exists()


def test_data_dir_inside_a_git_worktree_is_refused(tmp_path):
    repo = tmp_path / "some-repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(UnsafeDataDir):
        DataDir(repo / "data")
    with pytest.raises(UnsafeDataDir):
        DataDir(repo)


def test_data_dir_overlapping_a_forbidden_root_is_refused(tmp_path):
    forbidden = tmp_path / "projects"
    (forbidden / "x").mkdir(parents=True)
    with pytest.raises(UnsafeDataDir):
        DataDir(forbidden / "x" / "data", forbidden_roots=[forbidden])
    with pytest.raises(UnsafeDataDir):
        DataDir(tmp_path, forbidden_roots=[forbidden])  # parent of a forbidden root


def test_data_dir_must_not_be_a_symlink_home_or_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(real, tmp_path / "ln")
    with pytest.raises(UnsafeDataDir):
        DataDir(tmp_path / "ln")
    with pytest.raises(UnsafeDataDir):
        DataDir("/", create=False)
    with pytest.raises(UnsafeDataDir):
        DataDir(os.path.expanduser("~"), create=False)


def test_walk_does_not_follow_symlinks_out(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET")
    dd = DataDir(tmp_path / "d")
    dd.put_text("ok.txt", "x")
    os.symlink(outside, dd.root / "link")
    names = {p.name for p in dd.walk_files()}
    assert "secret.txt" not in names and "ok.txt" in names
