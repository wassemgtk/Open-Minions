"""
Rich display layer - modern Rich API for minion run output.

Uses Rich's Live, Status, Tree, Table, Logging, and Panel for
real-time feedback during runs, polished summaries, and structured logs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.logging import RichHandler
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.status import Status
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich.tree import Tree

# ── Theme ──────────────────────────────────────────────────

MINION_THEME = Theme({
    "minion.title": "bold bright_blue",
    "minion.success": "bold green",
    "minion.error": "bold red",
    "minion.warn": "bold yellow",
    "minion.info": "dim cyan",
    "minion.step": "bold white",
    "minion.branch": "bold magenta",
    "minion.tool": "cyan",
    "minion.file": "bright_yellow",
    "minion.muted": "dim white",
})

console = Console(theme=MINION_THEME)


def setup_logging(verbose: bool = False) -> None:
    """Configure rich-powered logging for the entire application."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                tracebacks_show_locals=verbose,
                show_path=verbose,
                markup=True,
            )
        ],
        force=True,
    )


# ── Banners & Headers ─────────────────────────────────────


def print_banner() -> None:
    """Print the Open Minions startup banner."""
    banner = Text()
    banner.append("  OPEN ", style="bold white on blue")
    banner.append(" MINIONS ", style="bold white on bright_blue")
    console.print()
    console.print(Panel(banner, subtitle="one-shot coding agents", border_style="bright_blue"))
    console.print()


def print_run_header(task: str, repo: Path, links: list[str] | None = None) -> None:
    """Print a formatted run header with task details."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", min_width=8)
    table.add_column()
    table.add_row("Task", Text(task, style="white"))
    table.add_row("Repo", Text(str(repo), style="minion.file"))
    if links:
        table.add_row("Links", Text(", ".join(links), style="minion.info"))

    console.print(Panel(table, title="[minion.title]Minion Run[/]", border_style="bright_blue"))


# ── Live Run Display ───────────────────────────────────────


class RunDisplay:
    """
    Real-time Rich display for a minion run.
    Shows spinner, current step, action log, and progress.
    """

    def __init__(self):
        self._actions: list[dict[str, Any]] = []
        self._current_step: str = "Initializing..."
        self._phase: str = "setup"
        self._live: Live | None = None

    def _build_display(self) -> Group:
        """Build the composite display for Live."""
        parts = []

        # Current phase status
        phase_text = Text()
        phase_text.append("  Phase: ", style="dim")
        phase_text.append(self._phase.upper(), style="minion.title")
        phase_text.append("  ", style="dim")
        parts.append(phase_text)

        # Current step with spinner indicator
        step_text = Text()
        step_text.append("  >>> ", style="bright_blue")
        step_text.append(self._current_step, style="minion.step")
        parts.append(step_text)

        # Recent actions (last 6)
        if self._actions:
            tree = Tree("[minion.info]Recent actions")
            for act in self._actions[-6:]:
                tool = act.get("tool", "?")
                result_preview = str(act.get("result", ""))[:60]
                label = Text()
                label.append(tool, style="minion.tool")
                label.append(f"  {result_preview}", style="minion.muted")
                tree.add(label)
            parts.append(Padding(tree, (1, 0, 0, 2)))

        return Group(*parts)

    def start(self) -> "RunDisplay":
        """Start the live display."""
        self._live = Live(
            self._build_display(),
            console=console,
            refresh_per_second=4,
            transient=True,
        )
        self._live.start()
        return self

    def stop(self) -> None:
        """Stop the live display."""
        if self._live:
            self._live.stop()
            self._live = None

    def update_step(self, step: str) -> None:
        self._current_step = step
        self._refresh()

    def update_phase(self, phase: str) -> None:
        self._phase = phase
        self._refresh()

    def add_action(self, action: dict[str, Any]) -> None:
        self._actions.append(action)
        self._refresh()

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._build_display())

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


# ── Run Summary ────────────────────────────────────────────


def print_run_summary(
    task: str,
    done: bool,
    branch: str | None,
    pr_url: str | None,
    actions: list[dict[str, Any]],
    ci_round: int = 0,
) -> None:
    """Print a polished summary table after a run completes."""
    console.print()
    console.print(Rule("[minion.title]Run Summary[/]"))

    # Status
    if done:
        console.print(Text("  Status: COMPLETED", style="minion.success"))
    else:
        console.print(Text("  Status: STOPPED", style="minion.warn"))

    # Details table
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold", min_width=10)
    details.add_column()

    details.add_row("Task", escape(task[:100]))
    if branch:
        details.add_row("Branch", Text(branch, style="minion.branch"))
    if pr_url:
        details.add_row("PR", Text(pr_url, style="bold underline cyan"))
    if ci_round > 0:
        details.add_row("CI Rounds", str(ci_round))

    console.print(Padding(details, (1, 0, 0, 2)))

    # Actions breakdown
    if actions:
        action_table = Table(
            title="Actions",
            show_header=True,
            header_style="bold",
            border_style="dim",
            min_width=60,
        )
        action_table.add_column("#", style="dim", width=4)
        action_table.add_column("Tool", style="minion.tool", min_width=12)
        action_table.add_column("Result", style="minion.muted", max_width=60, overflow="ellipsis")

        for i, act in enumerate(actions[-10:], 1):
            tool = act.get("tool", "?")
            result = str(act.get("result", ""))[:80]
            action_table.add_row(str(i), tool, result)

        console.print()
        console.print(Padding(action_table, (0, 0, 0, 2)))

    console.print()


# ── Status Context Manager ─────────────────────────────────


def status_spinner(message: str = "Minion working...") -> Status:
    """Get a Rich Status spinner for simple operations."""
    return console.status(message, spinner="dots")


# ── Progress for multi-step operations ─────────────────────


def create_progress() -> Progress:
    """Create a rich Progress bar for multi-step operations."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )


# ── Tree view for context ─────────────────────────────────


def print_context_tree(
    rules_count: int,
    mcp_tools: int,
    links: int,
    thread_messages: int = 0,
) -> None:
    """Print a tree showing what context was hydrated."""
    tree = Tree("[minion.title]Context Hydrated")
    if rules_count:
        tree.add(f"[minion.tool]{rules_count}[/] agent rules loaded")
    if mcp_tools:
        tree.add(f"[minion.tool]{mcp_tools}[/] MCP tool outputs")
    if links:
        tree.add(f"[minion.tool]{links}[/] links fetched")
    if thread_messages:
        tree.add(f"[minion.tool]{thread_messages}[/] Slack thread messages")
    console.print(Padding(tree, (0, 0, 0, 2)))
    console.print()


# ── Error display ──────────────────────────────────────────


def print_error(message: str, detail: str = "") -> None:
    """Print a formatted error."""
    err = Text()
    err.append("ERROR: ", style="minion.error")
    err.append(message)
    if detail:
        err.append(f"\n{detail}", style="minion.muted")
    console.print(Panel(err, border_style="red"))


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(Text(f"  {message}", style="minion.success"))
