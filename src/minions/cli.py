"""CLI entry point for minion runs - with modern Rich display."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from minions.config import MinionConfig
from minions.display import (
    RunDisplay,
    console,
    print_banner,
    print_context_tree,
    print_error,
    print_run_header,
    print_run_summary,
    print_success,
    setup_logging,
    status_spinner,
)
from minions.orchestrator import Orchestrator

app = typer.Typer(
    name="minion",
    help="One-shot, end-to-end coding agents. Task in, PR out.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _find_repo_root(start: Path) -> Path | None:
    """Find git repo root from start path."""
    p = start.resolve()
    for _ in range(20):
        if (p / ".git").exists():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    return None


# ── Main Commands ──────────────────────────────────────────


@app.command()
def run(
    task: str = typer.Argument(..., help="The task for the minion to complete"),
    repo: Path = typer.Option(
        Path.cwd(),
        "--repo", "-r",
        path_type=Path,
        help="Repository path (default: current directory)",
    ),
    links: str = typer.Option("", "--links", "-l", help="Comma-separated URLs for context"),
    ticket: str = typer.Option("", "--ticket", "-t", help="Ticket ID for context"),
    create_pr: bool = typer.Option(False, "--create-pr", "-p", help="Create PR when done"),
    github_token: str = typer.Option("", "--github-token", envvar="GITHUB_TOKEN", help="GitHub token for PR API"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
):
    """Run a minion on a task. Creates a branch and PR when done."""
    setup_logging(verbose)
    print_banner()

    repo = repo.resolve()
    root = _find_repo_root(repo)
    if not root:
        print_error("Not a git repository", "Run from inside a repo or pass --repo.")
        raise typer.Exit(1)

    config = MinionConfig.discover(root)
    links_list = [u.strip() for u in links.split(",") if u.strip()] if links else []

    print_run_header(task, root, links_list or None)

    async def _run():
        orch = Orchestrator(config, root)

        display = RunDisplay()
        with display:
            display.update_phase("context hydration")
            display.update_step("Loading agent rules and MCP context...")

            state = await orch.run(
                task=task,
                links=links_list or None,
                ticket_id=ticket or None,
                create_pr_after=create_pr,
                github_token=github_token or None,
                on_action=display.add_action,
                on_step=display.update_step,
                on_phase=display.update_phase,
            )

        return state

    state = asyncio.run(_run())

    # PR URL from actions
    pr_action = next((a for a in state.actions if a.get("tool") == "create_pr"), None)
    pr_url = pr_action.get("result") if pr_action else None

    print_run_summary(
        task=task,
        done=state.done,
        branch=state.branch_name,
        pr_url=pr_url,
        actions=state.actions,
        ci_round=state.ci_round,
    )


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8080, "--port"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Start the web UI server."""
    import uvicorn

    setup_logging(verbose)
    print_banner()
    console.print(f"  Starting web UI at [bold underline]http://{host}:{port}[/]\n")
    uvicorn.run("minions.web:app", host=host, port=port, reload=False)


@app.command()
def init(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", path_type=Path),
):
    """Initialize minion config in the repo."""
    root = repo.resolve()
    config_dir = root / ".minions"
    config_dir.mkdir(exist_ok=True)
    config_file = config_dir / "config.yaml"

    if config_file.exists():
        console.print(f"  Config already exists at [minion.file]{config_file}[/]")
        return

    example = """\
# Open Minions configuration

llm:
  provider: anthropic
  model: claude-sonnet-4-20250514
  fallback_provider: openai
  fallback_model: gpt-4o

slack:
  enabled: false
  # bot_token: xoxb-...
  # app_token: xapp-...
  # default_repo: /path/to/repo

github:
  # token: ghp_...  (or set GITHUB_TOKEN env var)
  # Set owner/repo or auto-detect from git remote
  auto_detect: true

mcp:
  enabled: true
  servers: []
  # Example:
  # - name: filesystem
  #   command: npx
  #   args: ["-y", "@modelcontextprotocol/server-filesystem", "."]

git:
  branch_prefix: minion/
  max_ci_rounds: 2
  base_branch: main
"""
    config_file.write_text(example)
    print_success(f"Created {config_file}")
    console.print("  Edit to add LLM keys, Slack tokens, GitHub config, etc.")


# ── Slack Command ──────────────────────────────────────────


@app.command()
def slack(
    repo: str = typer.Option(".", "--repo", "-r", help="Default repo path for Slack-triggered runs"),
    bot_token: str = typer.Option("", "--bot-token", envvar="SLACK_BOT_TOKEN"),
    app_token: str = typer.Option("", "--app-token", envvar="SLACK_APP_TOKEN"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Start the Slack bot (Socket Mode)."""
    setup_logging(verbose)
    print_banner()

    if not bot_token or not app_token:
        print_error(
            "Slack tokens required",
            "Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN, or pass --bot-token and --app-token.",
        )
        raise typer.Exit(1)

    config = MinionConfig.discover(Path(repo).resolve())
    console.print("  Starting Slack bot in Socket Mode...\n")

    from minions.integrations.slack_bot import SlackBot

    bot = SlackBot(
        slack_bot_token=bot_token,
        slack_app_token=app_token,
        default_repo_path=repo,
        config=config,
    )
    bot.run_sync()


# ── GitHub Command ─────────────────────────────────────────


@app.command()
def github(
    action: str = typer.Argument(..., help="Action: issue, pr-status, checks"),
    number: int = typer.Argument(..., help="Issue or PR number"),
    repo: Path = typer.Option(Path.cwd(), "--repo", "-r", path_type=Path),
    token: str = typer.Option("", "--token", envvar="GITHUB_TOKEN"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Interact with GitHub: fetch issues, check PR status, view CI."""
    from rich.table import Table
    from rich.tree import Tree

    setup_logging(verbose)

    if not token:
        print_error("GitHub token required", "Set GITHUB_TOKEN or pass --token.")
        raise typer.Exit(1)

    root = _find_repo_root(repo.resolve())
    if not root:
        print_error("Not a git repository")
        raise typer.Exit(1)

    async def _gh():
        from minions.integrations.github_client import GitHubClient
        import subprocess

        # Auto-detect owner/repo from git remote
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True,
        )
        remote_url = r.stdout.strip()
        if not remote_url:
            print_error("Cannot detect GitHub remote")
            raise typer.Exit(1)

        gh = GitHubClient.from_remote_url(token, remote_url)

        try:
            if action == "issue":
                issue = await gh.get_issue(number)
                tree = Tree(f"[bold]Issue #{issue.number}[/]: {issue.title}")
                tree.add(f"[dim]URL:[/] {issue.html_url}")
                tree.add(f"[dim]Labels:[/] {', '.join(issue.labels) or 'none'}")
                body_node = tree.add("[dim]Body[/]")
                body_node.add(issue.body[:500] if issue.body else "(empty)")
                if issue.comments:
                    comments_node = tree.add(f"[dim]Comments ({len(issue.comments)})[/]")
                    for c in issue.comments[:5]:
                        comments_node.add(f"@{c['user']}: {c['body'][:100]}")
                console.print(tree)

            elif action == "pr-status":
                pr = await gh.get_pull_request(number)
                table = Table(title=f"PR #{pr.number}")
                table.add_column("Field", style="bold")
                table.add_column("Value")
                table.add_row("Title", pr.title)
                table.add_row("State", pr.state)
                table.add_row("Branch", f"{pr.branch} -> {pr.base}")
                table.add_row("URL", pr.html_url)
                console.print(table)

            elif action == "checks":
                status = await gh.get_check_status(f"refs/pull/{number}/head")
                table = Table(title=f"CI Checks for PR #{number}")
                table.add_column("Status", style="bold")
                table.add_column("Total")
                table.add_column("Passed", style="green")
                table.add_column("Failed", style="red")
                table.add_row(status.state, str(status.total), str(status.passed), str(status.failed))
                console.print(table)
                if status.failures:
                    for f in status.failures:
                        console.print(f"  [red]FAIL[/] {f['name']}: {f['output'][:100]}")

            else:
                print_error(f"Unknown action: {action}", "Use: issue, pr-status, checks")

        finally:
            await gh.close()

    asyncio.run(_gh())


def main():
    app()


if __name__ == "__main__":
    main()
