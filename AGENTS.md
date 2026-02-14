# Agent Rules for Open Minions

These rules are consumed by minions (and other coding agents like Cursor/Claude). Rules are conditionally applied based on the subdirectory being edited.

## General Guidelines

- Make minimal, focused changes. Prefer small PRs.
- Follow existing code style and patterns in the codebase.
- Run linters before considering the task complete.
- When adding dependencies, prefer the project's existing stack (see pyproject.toml).

## Python

- Use type hints and `from __future__ import annotations` where applicable.
- Prefer `pathlib.Path` over string paths.
- Use `ruff` for linting; format with `ruff format`.
- Use `os.environ.get()` for environment variables, never `__import__("os")`.

## Project Layout

- `src/minions/` - Core minion package
- `src/minions/tools/` - Deterministic tools (git, lint, PR)
- `src/minions/integrations/` - Slack bot, GitHub client
- `src/minions/display.py` - Rich display layer
- `src/minions/web.py` - FastAPI web UI and GitHub webhook handler
- Config: `.minions/config.yaml`
