"""PR creation - open a pull request after minion completes."""

import subprocess
from pathlib import Path


def create_pr(
    repo_path: Path,
    branch: str,
    title: str,
    body: str | None = None,
    base: str = "main",
) -> str | None:
    """
    Create a PR using GitHub CLI (gh).
    Returns PR URL or None if gh not available/fails.
    """
    try:
        cmd = [
            "gh", "pr", "create",
            "--base", base,
            "--head", branch,
            "--title", title[:200],
        ]
        if body:
            cmd.extend(["--body", body])
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
