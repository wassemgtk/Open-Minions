"""Agent rules loading - conditionally applied by subdirectory."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterator


def load_rules_for_path(
    repo_root: Path,
    rule_patterns: list[str],
    target_path: Path | None = None,
    conditional: bool = True,
) -> str:
    """
    Load agent rules applicable to target_path.
    If conditional=True, only include rules whose glob matches the target subdir.
    """
    if target_path and (target_path == repo_root or repo_root in target_path.parents):
        target_str = str(target_path.relative_to(repo_root)).replace("\\", "/")
    else:
        target_str = "."

    collected: list[tuple[Path, str]] = []

    for pattern in rule_patterns:
        if "*" in pattern:
            base = repo_root
            for p in base.rglob(pattern.split("*")[0].rstrip("/") + "*"):
                if not p.is_file():
                    continue
                if not fnmatch.fnmatch(str(p.relative_to(base)).replace("\\", "/"), pattern):
                    continue
                if conditional and not _path_matches_target(p, repo_root, target_str):
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    collected.append((p, text))
                except OSError:
                    pass
        else:
            p = repo_root / pattern
            if p.exists() and p.is_file():
                if conditional and not _path_matches_target(p, repo_root, target_str):
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    collected.append((p, text))
                except OSError:
                    pass

    if not collected:
        return ""

    sections = []
    for path, text in collected:
        name = str(path.relative_to(repo_root))
        sections.append(f"## Rules from {name}\n\n{text}")

    return "\n\n---\n\n".join(sections)


def _path_matches_target(rule_path: Path, repo_root: Path, target_str: str) -> bool:
    """Check if rule applies to target. Rules can specify subdirs in their path or content."""
    rel = str(rule_path.relative_to(repo_root)).replace("\\", "/")
    if "**" in rel or "*" in rel:
        return True
    rule_dir = str(rule_path.parent.relative_to(repo_root)).replace("\\", "/")
    if not rule_dir or rule_dir == ".":
        return True
    return target_str.startswith(rule_dir + "/") or target_str == rule_dir


def discover_rule_files(repo_root: Path, patterns: list[str]) -> Iterator[Path]:
    """Discover all rule files matching patterns."""
    seen: set[str] = set()
    for pattern in patterns:
        if "*" in pattern:
            base_dir = pattern.split("*")[0].rstrip("/")
            search = repo_root
            if base_dir:
                search = repo_root / base_dir
            if not search.exists():
                continue
            for p in search.rglob("*"):
                if p.is_file() and fnmatch.fnmatch(str(p.relative_to(repo_root)), pattern):
                    key = str(p.resolve())
                    if key not in seen:
                        seen.add(key)
                        yield p
        else:
            p = repo_root / pattern
            if p.exists() and p.is_file() and str(p.resolve()) not in seen:
                seen.add(str(p.resolve()))
                yield p
