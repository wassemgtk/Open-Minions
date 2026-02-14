"""Configuration for minion runs."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = Field(default="anthropic", description="anthropic | openai")
    model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Model identifier",
    )
    fallback_provider: str | None = Field(default="openai", description="Fallback if primary fails")
    fallback_model: str | None = Field(default="gpt-4o", description="Fallback model")


class MCPServerConfig(BaseModel):
    """MCP server configuration."""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)


class MCPConfig(BaseModel):
    """MCP integration configuration."""

    enabled: bool = True
    servers: list[MCPServerConfig] = Field(default_factory=list)


class GitConfig(BaseModel):
    """Git and CI configuration."""

    branch_prefix: str = "minion/"
    max_ci_rounds: int = Field(default=2, ge=1, le=5)
    remote: str = "origin"
    base_branch: str = "main"


class AgentRulesConfig(BaseModel):
    """Agent rules configuration."""

    paths: list[str] = Field(
        default_factory=lambda: [".cursor/rules/*.mdc", "AGENTS.md", ".cursorrules"],
        description="Glob patterns for rule files",
    )
    conditional_by_subdir: bool = True


class SlackConfig(BaseModel):
    """Slack bot configuration."""

    enabled: bool = False
    bot_token: str = Field(default="", description="Slack bot token (xoxb-...)")
    app_token: str = Field(default="", description="Slack app token for Socket Mode (xapp-...)")
    default_repo: str = Field(default=".", description="Default repo path for Slack-triggered runs")

    @property
    def bot_token_resolved(self) -> str:
        return self.bot_token or os.environ.get("SLACK_BOT_TOKEN", "")

    @property
    def app_token_resolved(self) -> str:
        return self.app_token or os.environ.get("SLACK_APP_TOKEN", "")


class GitHubConfig(BaseModel):
    """GitHub integration configuration."""

    token: str = Field(default="", description="GitHub personal access token")
    auto_detect: bool = Field(default=True, description="Auto-detect owner/repo from git remote")
    owner: str = Field(default="", description="GitHub owner (if not auto-detecting)")
    repo: str = Field(default="", description="GitHub repo name (if not auto-detecting)")
    webhook_secret: str = Field(default="", description="Webhook secret for verifying events")
    create_pr_on_complete: bool = Field(default=False, description="Auto-create PR when minion finishes")
    wait_for_ci: bool = Field(default=False, description="Wait for CI checks after pushing")
    ci_timeout_seconds: int = Field(default=600, description="Max seconds to wait for CI")

    @property
    def token_resolved(self) -> str:
        return self.token or os.environ.get("GITHUB_TOKEN", "")


class MinionConfig(BaseModel):
    """Full minion configuration."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    agent_rules: AgentRulesConfig = Field(default_factory=AgentRulesConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)

    @classmethod
    def from_file(cls, path: Path) -> MinionConfig:
        """Load config from YAML file."""
        if not path.exists():
            return cls()
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    @classmethod
    def discover(cls, repo_root: Path) -> MinionConfig:
        """Discover config from repo or default locations."""
        candidates = [
            repo_root / ".minions" / "config.yaml",
            repo_root / "minions.yaml",
            Path.home() / ".minions" / "config.yaml",
        ]
        for p in candidates:
            if p.exists():
                return cls.from_file(p)
        return cls()


