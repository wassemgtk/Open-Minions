"""Git operations - branch, commit, push."""

import subprocess
from pathlib import Path

from minions.config import GitConfig


class GitTools:
    """Deterministic git operations for minion runs."""

    def __init__(self, repo_path: Path, config: GitConfig):
        self.repo_path = Path(repo_path)
        self.config = config

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=check,
        )

    def current_branch(self) -> str:
        result = self._run("branch", "--show-current")
        return result.stdout.strip() or "HEAD"

    def create_branch(self, name: str) -> str:
        """Create and checkout a new branch. Returns branch name."""
        branch = f"{self.config.branch_prefix}{name}"
        self._run("checkout", "-b", branch)
        return branch

    def stage_and_commit(self, message: str, paths: list[str] | None = None) -> None:
        """Stage and commit. If paths is None, stage all."""
        if paths:
            for p in paths:
                self._run("add", str(p))
        else:
            self._run("add", "-A")
        self._run("commit", "-m", message)

    def push(self, branch: str | None = None) -> str:
        """Push branch to remote. Returns push output."""
        branch = branch or self.current_branch()
        result = self._run("push", "-u", self.config.remote, branch)
        return result.stdout + result.stderr

    def status(self) -> str:
        result = self._run("status", "--short")
        return result.stdout

    def diff(self, paths: list[str] | None = None) -> str:
        args = ["diff", "--no-color"]
        if paths:
            args.extend(paths)
        result = self._run(*args)
        return result.stdout

    def has_changes(self) -> bool:
        result = self._run("status", "--porcelain")
        return bool(result.stdout.strip())

    def fetch_latest(self) -> None:
        self._run("fetch", self.config.remote, self.config.base_branch)
