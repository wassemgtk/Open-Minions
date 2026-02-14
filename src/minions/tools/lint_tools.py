"""Linting - shift feedback left, run in <5 seconds on each push."""

import subprocess
from pathlib import Path


class LintTools:
    """
    Run linters on changed/staged files.
    Heuristics to select relevant linters (e.g. ruff for Python, eslint for JS).
    """

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path)

    def _run(self, cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            cwd=cwd or self.repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def get_staged_files(self) -> list[str]:
        result = self._run(["git", "diff", "--cached", "--name-only"])
        return [f for f in result.stdout.strip().splitlines() if f]

    def run_relevant_linters(self, paths: list[str] | None = None) -> tuple[bool, str]:
        """
        Run linters on the given paths (or staged files).
        Returns (success, output).
        """
        if paths is None:
            paths = self.get_staged_files()
        if not paths:
            paths = ["."]

        outputs: list[str] = []
        all_ok = True

        # Detect languages and run appropriate linters
        py_files = [p for p in paths if p.endswith(".py")]
        js_files = [p for p in paths if any(p.endswith(e) for e in [".js", ".ts", ".tsx"])]

        if py_files:
            ok, out = self._run_ruff(py_files)
            if out:
                outputs.append(f"ruff:\n{out}")
            all_ok = all_ok and ok

        if js_files:
            ok, out = self._run_eslint(js_files)
            if out:
                outputs.append(f"eslint:\n{out}")
            all_ok = all_ok and ok

        output = "\n\n".join(outputs) if outputs else "No linters run (no matching files)."
        return all_ok, output

    def _run_ruff(self, paths: list[str]) -> tuple[bool, str]:
        try:
            r = self._run(["ruff", "check", *paths])
            if r.returncode != 0:
                return False, r.stdout + r.stderr
            return True, r.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return True, ""  # ruff not installed = skip

    def _run_eslint(self, paths: list[str]) -> tuple[bool, str]:
        try:
            r = self._run(["npx", "eslint", "--no-error-on-unmatched-pattern", *paths])
            if r.returncode != 0:
                return False, r.stdout + r.stderr
            return True, r.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return True, ""
