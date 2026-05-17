"""GitManager tests — real Git in a temporary directory."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from forgeai.delivery.git_manager import GitManager, resolve_git_executable


def _git_available() -> bool:
    return resolve_git_executable() is not None


pytestmark = pytest.mark.skipif(
    not _git_available(),
    reason="git executable not found (install Git or set GIT_EXECUTABLE)",
)


@pytest.fixture
def git_exe() -> str:
    path = resolve_git_executable()
    assert path is not None
    return path


@pytest.fixture
def repo_tmp(tmp_path: Path) -> Path:
    return tmp_path / "project"


def test_init_repo_creates_git_directory(repo_tmp: Path, git_exe: str) -> None:
    git = GitManager(str(repo_tmp), git_executable=git_exe)
    git.init_repo()
    assert (repo_tmp / ".git").is_dir()
    assert (repo_tmp / ".gitignore").is_file()


def test_commit_creates_real_git_commit(repo_tmp: Path, git_exe: str) -> None:
    git = GitManager(str(repo_tmp), git_executable=git_exe)
    git.init_repo()
    sample = repo_tmp / "hello.txt"
    sample.write_text("hi", encoding="utf-8")
    commit = git.commit("task-1", "agent-1", "section-a", ["hello.txt"])
    assert len(commit.hash) >= 7
    log = subprocess.run(
        [git_exe, "log", "-1", "--oneline"],
        cwd=repo_tmp,
        capture_output=True,
        text=True,
        check=True,
    )
    assert commit.hash[:7] in log.stdout


def test_create_tag_creates_real_git_tag(repo_tmp: Path, git_exe: str) -> None:
    git = GitManager(str(repo_tmp), git_executable=git_exe)
    git.init_repo()
    (repo_tmp / "a.txt").write_text("a", encoding="utf-8")
    git.commit("t1", "a1", "s", ["a.txt"])
    point = git.create_tag("release-v1", "First release")
    assert point.tag_name == "release-v1"
    tags = subprocess.run(
        [git_exe, "tag", "-l"],
        cwd=repo_tmp,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "release-v1" in tags.stdout


def test_get_log_returns_commits_reverse_chronological(
    repo_tmp: Path, git_exe: str
) -> None:
    git = GitManager(str(repo_tmp), git_executable=git_exe)
    git.init_repo()
    (repo_tmp / "one.txt").write_text("1", encoding="utf-8")
    git.commit("t1", "a1", "s1", ["one.txt"])
    (repo_tmp / "two.txt").write_text("2", encoding="utf-8")
    git.commit("t2", "a2", "s2", ["two.txt"])
    commits = git.get_log(max_entries=10)
    assert len(commits) >= 2
    assert commits[0].timestamp >= commits[1].timestamp


def test_get_tags_returns_created_tags(repo_tmp: Path, git_exe: str) -> None:
    git = GitManager(str(repo_tmp), git_executable=git_exe)
    git.init_repo()
    (repo_tmp / "f.txt").write_text("x", encoding="utf-8")
    git.commit("t", "a", "s", ["f.txt"])
    git.create_tag("milestone-a", "Milestone A")
    tags = git.get_tags()
    names = {t.tag_name for t in tags}
    assert "milestone-a" in names


def test_rollback_to_tag_restores_repository_state(repo_tmp: Path, git_exe: str) -> None:
    git = GitManager(str(repo_tmp), git_executable=git_exe)
    git.init_repo()
    baseline = repo_tmp / "base.txt"
    baseline.write_text("v1", encoding="utf-8")
    git.commit("t1", "a1", "s1", ["base.txt"])
    git.create_tag("stable", "Stable")
    evolved = repo_tmp / "next.txt"
    evolved.write_text("v2", encoding="utf-8")
    git.commit("t2", "a2", "s2", ["next.txt"])
    assert evolved.exists()
    git.rollback_to_tag("stable")
    assert not evolved.exists()
    assert baseline.read_text(encoding="utf-8") == "v1"
