"""Git operations for deployment output (Phase 10, Req 19)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from forgeai.delivery.schemas import GitCommit, RollbackPoint

logger = logging.getLogger(__name__)

_WINDOWS_GIT_PATHS = (
    Path(r"C:\Program Files\Git\cmd\git.exe"),
    Path(r"C:\Program Files (x86)\Git\cmd\git.exe"),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe")),
)


def resolve_git_executable() -> str | None:
    """Locate ``git`` on PATH or common install locations (Windows-friendly)."""
    for env_key in ("GIT_EXECUTABLE", "GIT_PATH"):
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            candidate = Path(env_val)
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("git")
    if found:
        return found
    for path in _WINDOWS_GIT_PATHS:
        if path.is_file():
            return str(path)
    return None

_GITIGNORE = """\
__pycache__/
*.pyc
.env
node_modules/
dist/
.venv/
*.egg-info/
.DS_Store
"""


class GitManager:
    """Manage a real Git repository for project delivery output."""

    def __init__(self, repo_path: str, *, git_executable: str | None = None) -> None:
        self.repo_path = Path(repo_path)
        self._git = git_executable or resolve_git_executable()
        if not self._git:
            raise RuntimeError(
                "Git executable not found. Install Git or set GIT_EXECUTABLE."
            )

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self._git, *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=check,
        )

    def init_repo(self) -> None:
        """Initialise repository with ForgeAI identity and .gitignore."""
        self.repo_path.mkdir(parents=True, exist_ok=True)
        if not (self.repo_path / ".git").exists():
            self._run("init")
        self._run("config", "user.email", "forgeai@local")
        self._run("config", "user.name", "ForgeAI")
        gitignore = self.repo_path / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE, encoding="utf-8")

    def commit(
        self,
        task_id: str,
        agent_id: str,
        master_doc_section: str,
        files: list[str],
    ) -> GitCommit:
        """Stage files and create a task-scoped commit."""
        rel_files = [
            f for f in files if f and (self.repo_path / f).exists()
        ]
        if not rel_files:
            raise ValueError("commit() requires at least one existing file path")
        for path in rel_files:
            self._run("add", path)
        message = (
            f"task:{task_id} agent:{agent_id}\n"
            f"section:{master_doc_section}"
        )
        self._run("commit", "-m", message)
        show = self._run("show", "-s", "--format=%H|%an|%aI", "HEAD")
        parts = show.stdout.strip().split("|", 2)
        commit_hash = parts[0] if parts else ""
        author = parts[1] if len(parts) > 1 else "ForgeAI"
        ts_raw = parts[2] if len(parts) > 2 else ""
        try:
            timestamp = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            timestamp = datetime.now(UTC)
        logger.info("Committed %s: %s", commit_hash[:8], message.split("\n")[0])
        return GitCommit(
            hash=commit_hash,
            message=message,
            author=author,
            timestamp=timestamp,
            task_id=task_id or None,
            agent_id=agent_id or None,
        )

    def create_tag(self, tag_name: str, message: str) -> RollbackPoint:
        """Create an annotated tag at HEAD."""
        self._run("tag", "-a", tag_name, "-m", message)
        show = self._run("rev-parse", "HEAD")
        commit_hash = show.stdout.strip()
        logger.info("Tagged %s", tag_name)
        return RollbackPoint(
            tag_name=tag_name,
            message=message,
            created_at=datetime.now(UTC),
            commit_hash=commit_hash,
        )

    def rollback_to_tag(self, tag_name: str) -> None:
        """Checkout a tag (destructive — human-initiated only)."""
        self._run("checkout", tag_name)

    def get_log(self, max_entries: int = 50) -> list[GitCommit]:
        """Return recent commits newest-first."""
        proc = self._run(
            "log",
            f"-n{max_entries}",
            "--format=%H|%s|%an|%aI",
            check=False,
        )
        commits: list[GitCommit] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            h, msg, author, ts_raw = parts
            task_id = None
            agent_id = None
            m_task = re.search(r"task:([^\s]+)", msg)
            m_agent = re.search(r"agent:([^\s]+)", msg)
            if m_task:
                task_id = m_task.group(1)
            if m_agent:
                agent_id = m_agent.group(1)
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                ts = datetime.now(UTC)
            commits.append(
                GitCommit(
                    hash=h,
                    message=msg,
                    author=author,
                    timestamp=ts,
                    task_id=task_id,
                    agent_id=agent_id,
                )
            )
        return commits

    def get_tags(self) -> list[RollbackPoint]:
        """List annotated and lightweight tags."""
        proc = self._run("tag", "-l", check=False)
        points: list[RollbackPoint] = []
        for tag in proc.stdout.splitlines():
            tag = tag.strip()
            if not tag:
                continue
            show = self._run("rev-list", "-n", "1", tag, check=False)
            commit_hash = show.stdout.strip() if show.returncode == 0 else ""
            msg_proc = self._run(
                "tag",
                "-l",
                "--format=%(contents)",
                tag,
                check=False,
            )
            message = msg_proc.stdout.strip() or tag
            points.append(
                RollbackPoint(
                    tag_name=tag,
                    message=message,
                    created_at=datetime.now(UTC),
                    commit_hash=commit_hash,
                )
            )
        return points
