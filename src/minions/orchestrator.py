"""
Core agent orchestrator - interleaves agent loops with deterministic steps.

Flow:
1. Hydrate context
2. Agent loop: plan -> edit -> deterministic lint -> (repeat or done)
3. Git: branch, commit, push
4. Local lint on push (shift feedback left)
5. CI (at most 2 rounds) - on failure, feed back to agent for one retry
6. Create PR via GitHub API or gh CLI
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from minions.config import MinionConfig
from minions.context import hydrate_context
from minions.llm import LLMClient
from minions.tools import GitTools, LintTools

logger = logging.getLogger("minions.orchestrator")


@dataclass
class RunState:
    """State for a minion run."""

    task: str
    repo_root: Path
    messages: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    branch_name: str | None = None
    pr_url: str | None = None
    ci_round: int = 0
    done: bool = False


class Orchestrator:
    """Orchestrates a minion run from task to PR."""

    def __init__(self, config: MinionConfig, repo_root: Path):
        self.config = config
        self.repo_root = Path(repo_root)
        self.llm = LLMClient(config.llm)
        self.git = GitTools(self.repo_root, config.git)
        self.lint = LintTools(self.repo_root)

    @property
    def repo_path(self) -> Path:
        return self.repo_root

    async def run(
        self,
        task: str,
        links: list[str] | None = None,
        ticket_id: str | None = None,
        create_pr_after: bool = False,
        github_token: str | None = None,
        on_action: Callable[[dict[str, Any]], None] | None = None,
        on_step: Callable[[str], None] | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> RunState:
        """Execute a full minion run."""
        state = RunState(task=task, repo_root=self.repo_root)
        _step = on_step or (lambda s: None)
        _phase = on_phase or (lambda p: None)
        _action = on_action or (lambda a: None)

        # 1. Hydrate context
        _phase("context hydration")
        _step("Loading agent rules and MCP context...")
        logger.info("Hydrating context for task: %s", task[:80])

        context = await hydrate_context(
            self.config,
            self.repo_root,
            task,
            links=links,
            ticket_id=ticket_id,
        )

        system = self._build_system_prompt(context)
        state.messages = [
            {"role": "user", "content": f"Task: {task}\n\nProceed to complete this task."}
        ]

        # 2. Agent loop (interleaved with deterministic steps)
        _phase("agent loop")
        max_turns = 20
        for turn in range(max_turns):
            _step(f"Agent turn {turn + 1}/{max_turns}...")
            logger.debug("Agent turn %d", turn + 1)

            response = await self.llm.complete(
                messages=state.messages,
                system=system,
                max_tokens=8192,
            )

            state.messages.append({"role": "assistant", "content": response})

            # Parse tool calls from response
            tool_calls = self._parse_tool_calls(response)
            if not tool_calls:
                if self._looks_done(response):
                    state.done = True
                    break
                state.messages.append({
                    "role": "user",
                    "content": (
                        "Please use the edit_file tool to make changes, "
                        "or the done tool if finished."
                    ),
                })
                continue

            tool_results: list[str] = []
            for tc in tool_calls:
                name = tc.get("name", "")
                params = tc.get("parameters", tc)
                _step(f"Executing tool: {name}")
                result = await self._execute_tool(name, params, state)

                action = {"tool": name, "params": params, "result": result}
                state.actions.append(action)
                _action(action)
                tool_results.append(f"{name}: {result}")

                if name == "done":
                    state.done = True
                    break

            # Feed tool results back to agent
            if tool_results:
                state.messages.append({
                    "role": "user",
                    "content": "Tool results:\n" + "\n".join(tool_results),
                })

            if state.done:
                break

            # Deterministic: run linter on edited files
            edited_paths = [
                tc.get("parameters", tc).get("path")
                for tc in tool_calls
                if tc.get("name") == "edit_file"
            ]
            edited_paths = [p for p in edited_paths if p]
            if edited_paths:
                _step("Running linters...")
                ok, lint_out = self.lint.run_relevant_linters(paths=edited_paths)
                if not ok:
                    logger.info("Lint failed, feeding back to agent")
                    state.messages.append({
                        "role": "user",
                        "content": f"Linter failed. Fix the issues:\n\n{lint_out}",
                    })

        # 3. Git: create branch, commit, push
        if state.done and self.git.has_changes():
            _phase("git")
            safe_name = re.sub(r"[^a-z0-9-]", "-", task[:40].lower()).strip("-")[:30]
            _step(f"Creating branch: {self.config.git.branch_prefix}{safe_name}")
            state.branch_name = self.git.create_branch(safe_name)

            self.git.stage_and_commit(f"minion: {task[:72]}")
            logger.info("Committed changes on branch %s", state.branch_name)

            # 4. Local lint on push (shift feedback left)
            _step("Local lint check before push...")
            # Collect all files the agent edited during the run
            all_edited = list({
                a["params"].get("path")
                for a in state.actions
                if a.get("tool") == "edit_file" and a.get("params", {}).get("path")
            })
            ok, lint_out = self.lint.run_relevant_linters(paths=all_edited or None)
            if not ok:
                logger.warning("Post-commit lint found issues")
                state.ci_round = 1

            _step("Pushing branch...")
            self.git.push(state.branch_name)
            logger.info("Pushed branch %s", state.branch_name)

            # 5. Create PR
            if create_pr_after:
                _phase("pull request")
                pr_url = await self._create_pr(
                    state, task, github_token
                )
                if pr_url:
                    state.pr_url = pr_url
                    state.actions.append({"tool": "create_pr", "result": pr_url})
                    _action({"tool": "create_pr", "result": pr_url})

        return state

    async def _create_pr(
        self,
        state: RunState,
        task: str,
        github_token: str | None,
    ) -> str | None:
        """Create a PR using GitHub API (preferred) or gh CLI (fallback)."""
        title = f"Minion: {task[:72]}"
        body = (
            f"Automated by [Open Minions](https://github.com/your-org/open-minions).\n\n"
            f"**Task:** {task}\n\n"
            f"**Actions:** {len(state.actions)} steps\n\n"
            f"---\n_This PR was created by a one-shot coding agent._"
        )

        # Try GitHub API first
        if github_token and state.branch_name:
            try:
                from minions.integrations.github_client import GitHubClient

                # Auto-detect owner/repo from git remote
                r = subprocess.run(
                    ["git", "remote", "get-url", self.config.git.remote],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True,
                )
                remote_url = r.stdout.strip()
                if remote_url:
                    gh = GitHubClient.from_remote_url(github_token, remote_url)
                    try:
                        pr = await gh.create_pull_request(
                            title=title,
                            head=state.branch_name,
                            base=self.config.git.base_branch,
                            body=body,
                        )
                        logger.info("Created PR #%d: %s", pr.number, pr.html_url)
                        return pr.html_url
                    finally:
                        await gh.close()
            except Exception as e:
                logger.warning("GitHub API PR creation failed: %s, falling back to gh CLI", e)

        # Fallback: gh CLI
        from minions.tools.pr_tools import create_pr

        return create_pr(
            self.repo_path,
            state.branch_name or "",
            title=title,
            body=body,
            base=self.config.git.base_branch,
        )

    @staticmethod
    def _looks_done(response: str) -> bool:
        """Heuristic: does this response signal the task is done?"""
        lower = response.lower()
        done_signals = [
            "task is complete",
            "task is done",
            "changes are complete",
            "all done",
            "i've completed",
            "i have completed",
        ]
        return any(sig in lower for sig in done_signals)

    def _build_system_prompt(self, context: str) -> str:
        return f"""You are a minion: a one-shot, unattended coding agent. Complete the task fully without asking for clarification.

## Context

{context}

## Tools

You have access to these tools. Use them by outputting a fenced block:
```minion_tool
{{"name": "tool_name", "parameters": {{...}}}}
```

Available tools:
- edit_file: Edit a file. Provide "path" (relative to repo root) and "content" (full file content).
- read_file: Read a file. Provide "path".
- run_shell: Run a shell command. Provide "command". Use for grepping, listing files, running tests, etc.
- done: Signal task complete. Provide "summary".

## Rules

1. Make minimal, focused changes.
2. Follow existing code style and patterns.
3. Run linters locally if you can; we'll also run them automatically after your edits.
4. When done, call the done tool with a brief summary of all changes.
"""

    def _parse_tool_calls(self, response: str) -> list[dict]:
        calls = []
        for m in re.finditer(r"```(?:minion_tool|json)?\s*(\{[\s\S]*?\})\s*```", response):
            try:
                obj = json.loads(m.group(1))
                if isinstance(obj, dict) and "name" in obj:
                    calls.append(obj)
            except Exception:
                pass
        return calls

    async def _execute_tool(
        self, name: str, params: dict, state: RunState
    ) -> str:
        path = (params.get("path") or "").strip()
        content = params.get("content", "")
        command = params.get("command", "")
        summary = params.get("summary", "")

        if name == "edit_file":
            if not path:
                return "Error: path required"
            full = self.repo_path / path
            try:
                full.parent.mkdir(parents=True, exist_ok=True)
                if content.strip().startswith("--- a/") or content.strip().startswith("diff --git"):
                    return self._apply_diff(path, content)
                full.write_text(content, encoding="utf-8")
                logger.debug("Wrote file: %s", path)
                return f"Wrote {path}"
            except Exception as e:
                return f"Error: {e}"

        elif name == "read_file":
            if not path:
                return "Error: path required"
            full = self.repo_path / path
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
                return text[:20_000]  # Cap to avoid blowing context
            except Exception as e:
                return f"Error: {e}"

        elif name == "run_shell":
            if not command:
                return "Error: command required"
            try:
                r = subprocess.run(
                    command,
                    shell=True,
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                output = (r.stdout + "\n" + r.stderr).strip() or f"exit {r.returncode}"
                return output[:10_000]  # Cap output
            except Exception as e:
                return f"Error: {e}"

        elif name == "done":
            return f"Done: {summary}"

        return f"Unknown tool: {name}"

    def _apply_diff(self, path: str, diff_content: str) -> str:
        """Apply a unified diff to a file."""
        try:
            from tempfile import NamedTemporaryFile

            with NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
                f.write(diff_content)
                tmp = f.name
            try:
                subprocess.run(
                    ["patch", "-p1", "--forward", f"--input={tmp}"],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                return f"Applied diff to {path}"
            finally:
                Path(tmp).unlink(missing_ok=True)
        except Exception as e:
            return f"Error applying diff: {e}"
