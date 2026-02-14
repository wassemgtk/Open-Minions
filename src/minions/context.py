"""Context hydration - MCP tools, agent rules, and pre-run context gathering."""

from pathlib import Path
from typing import Any

from minions.config import MinionConfig
from minions.rules import load_rules_for_path


async def hydrate_context(
    config: MinionConfig,
    repo_root: Path,
    task: str,
    links: list[str] | None = None,
    ticket_id: str | None = None,
    target_path: Path | None = None,
) -> str:
    """
    Hydrate context before agent run.
    Gathers: agent rules, MCP tool outputs for links, ticket details.
    """
    parts: list[str] = []

    # 1. Agent rules (conditional by subdir)
    rules = load_rules_for_path(
        repo_root,
        config.agent_rules.paths,
        target_path=target_path,
        conditional=config.agent_rules.conditional_by_subdir,
    )
    if rules:
        parts.append("# Agent Rules (applicable to this task)\n\n" + rules)

    # 2. MCP context (if enabled and servers configured)
    if config.mcp.enabled and config.mcp.servers:
        mcp_ctx = await _gather_mcp_context(config, repo_root, task, links or [], ticket_id)
        if mcp_ctx:
            parts.append("# Context from MCP tools\n\n" + mcp_ctx)

    # 3. Task framing
    parts.append(f"# Task\n\n{task}")

    if links:
        parts.append("\n# Provided links (for reference)\n\n" + "\n".join(f"- {u}" for u in links))

    if ticket_id:
        parts.append(f"\n# Ticket ID: {ticket_id}")

    return "\n\n---\n\n".join(parts)


async def _gather_mcp_context(
    config: MinionConfig,
    repo_root: Path,
    task: str,
    links: list[str],
    ticket_id: str | None,
) -> str:
    """
    Run relevant MCP tools over links and task to hydrate context.
    Uses heuristics: if link looks like a doc URL, fetch it; if ticket_id, fetch ticket.
    MCP is optional - if the SDK isn't available or servers fail, we degrade gracefully.
    """
    if not config.mcp.servers:
        return ""

    outputs: list[str] = []
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        return ""

    for server_cfg in config.mcp.servers:
        try:
            params = StdioServerParameters(
                command=server_cfg.command,
                args=[str(a).replace("{repo}", str(repo_root)) for a in server_cfg.args],
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    tools_list = getattr(tools_result, "tools", []) or []

                    for tool in tools_list:
                        tname = getattr(tool, "name", str(tool))
                        if _is_relevant_tool(tname, task, links, ticket_id):
                            try:
                                result = await session.call_tool(tname, {"task": task})
                                content = getattr(result, "content", []) or []
                                for c in content:
                                    if hasattr(c, "text"):
                                        outputs.append(f"### {tname}\n\n{c.text}")
                            except Exception:
                                pass
        except Exception:
            pass

    return "\n\n".join(outputs) if outputs else ""


def _is_relevant_tool(tool_name: str, task: str, links: list[str], ticket_id: str | None) -> bool:
    """Heuristic: should we run this MCP tool before the run?"""
    name_lower = tool_name.lower()
    task_lower = task.lower()
    if "search" in name_lower and ("find" in task_lower or "search" in task_lower):
        return True
    if "doc" in name_lower and links:
        return True
    if "ticket" in name_lower and ticket_id:
        return True
    if "read" in name_lower or "fetch" in name_lower:
        return bool(links)
    return False
