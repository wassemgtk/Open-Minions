# Open Minions

**One-shot, end-to-end coding agents.** Unattended agents that turn a task into a pull request -- no human interaction in between.

Inspired by [Stripe's Minions](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents) -- homegrown coding agents that merge over a thousand PRs weekly at Stripe with no human-written code.

## What Minions Do

A typical minion run:

1. **Receives a task** (CLI, web UI, Slack, or GitHub webhook)
2. **Hydrates context** via MCP (docs, code search, tickets) and agent rules
3. **Runs an agent loop** that interleaves LLM creativity with deterministic steps
4. **Shifts feedback left**: runs linters on edited files before pushing
5. **Creates a branch**, pushes, and opens a pull request ready for review

```
Task -> Context Hydration -> Agent Loop <-> Lint/Test -> Branch -> PR
```

## Features

- **One-shot completion**: Task in, PR out
- **Slack bot**: @-mention in a thread, minion reads the full thread as context and posts back the PR
- **GitHub integration**: Full API (create PRs, read issues, check CI), webhooks (label an issue `minion` or comment `/minion`), CLI commands
- **MCP integration**: Gather context from docs, code search, tickets via Model Context Protocol
- **Agent rules**: Conditionally applied coding standards (Cursor rules, AGENTS.md, .cursorrules)
- **Shift feedback left**: Linters run on edited files in under 5 seconds
- **At most 2 CI rounds**: Minion gets one retry on test failures, then stops
- **Modern Rich UI**: Live displays, progress trees, themed tables, structured logging
- **Multiple entry points**: CLI, web UI, Slack bot, GitHub webhooks

## Quick Start

```bash
pip install open-minions
minion setup
minion run "Add a retry decorator to the fetch_user function in api/client.py"
```

That's it. `minion setup` prompts for your API key and saves it globally. GitHub token is auto-detected from `gh` CLI if you're logged in.

## Usage

### CLI

```bash
# Run a task in current repo
minion run "Fix the type error in utils/validators.py"

# Target a different repo and create a PR
minion run "Add input validation" --repo ~/my-project --create-pr

# Provide additional context via links
minion run "Update the auth flow" --links "https://docs.example.com/auth"
```

### Web UI

```bash
minion serve
# -> http://localhost:8080
```

### Slack Bot

```bash
minion slack --repo ~/my-project
```

Requires `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` (via `.env` or environment). Engineers @-mention the bot in a Slack thread — it reads the full thread and posts the PR link back.

### GitHub CLI

GitHub token is auto-detected from `gh` CLI. Otherwise, set it via `minion setup`.

```bash
minion github issue 42          # View an issue
minion github pr-status 100     # Check PR status
minion github checks 100        # View CI results
```

### GitHub Webhooks

Point your repo's webhook to `/webhooks/github/events`. Triggers:
- Label an issue with `minion`
- Comment `/minion <task>` on an issue

## Configuration

### API Keys

Run `minion setup` to interactively configure API keys. Keys are saved to `~/.minions/.env` (global, works across all repos). You can also:

- Set environment variables directly: `export ANTHROPIC_API_KEY=...`
- Use a project-level `.env` file (auto-loaded, overrides global keys)
- See `.env.example` for all supported variables

Key resolution order: project `.env` > global `~/.minions/.env` > shell environment.

### Project Config (optional)

Minions work with sensible defaults. To customize per-repo settings, run `minion init`:

```bash
minion init --repo ~/my-project
```

This creates `.minions/config.yaml`:

```yaml
# .minions/config.yaml

llm:
  provider: anthropic
  model: claude-sonnet-4-20250514
  fallback_provider: openai
  fallback_model: gpt-4o

slack:
  enabled: false
  # bot_token: xoxb-...        (or SLACK_BOT_TOKEN env var)
  # app_token: xapp-...        (or SLACK_APP_TOKEN env var)
  # default_repo: /path/to/repo

github:
  # token: ghp_...             (or GITHUB_TOKEN env var)
  auto_detect: true            # detect owner/repo from git remote
  # owner: your-org            # manual override
  # repo: your-repo
  create_pr_on_complete: false
  wait_for_ci: false

mcp:
  enabled: true
  servers: []
  # - name: filesystem
  #   command: npx
  #   args: ["-y", "@modelcontextprotocol/server-filesystem", "."]

git:
  branch_prefix: minion/
  max_ci_rounds: 2
  remote: origin
  base_branch: main

agent_rules:
  paths:
    - ".cursor/rules/*.mdc"
    - "AGENTS.md"
    - ".cursorrules"
  conditional_by_subdir: true
```

## Architecture

```
+-----------------------------------------------------------------+
|                     Entry Points                                |
|  CLI (minion run)  |  Web UI  |  Slack Bot  |  GitHub Webhooks  |
+-----------------------------+-----------------------------------+
                              |
                              v
+-----------------------------------------------------------------+
|                   Context Hydration                             |
|  MCP tools  |  Agent rules (conditional)  |  Thread/Issue data  |
+-----------------------------+-----------------------------------+
                              |
                              v
+-----------------------------------------------------------------+
|                  Orchestrator (Agent Loop)                      |
|  +-------------+     +--------------+     +-----------------+  |
|  | LLM Agent   | <-> | Deterministic| <-> | Feedback Loop   |  |
|  | (plan/edit) |     | (git/lint)   |     | (local -> CI)   |  |
|  +-------------+     +--------------+     +-----------------+  |
+-----------------------------+-----------------------------------+
                              |
                              v
+-----------------------------------------------------------------+
|                   Output: Branch + PR                           |
|  Create branch -> Push -> GitHub API PR (or gh CLI fallback)   |
+-----------------------------------------------------------------+
```

## Project Structure

```
open-minions/
├── src/minions/
│   ├── orchestrator.py        # Core agent loop with deterministic steps
│   ├── context.py             # Context hydration (MCP + rules)
│   ├── llm.py                 # LLM provider abstraction (Anthropic/OpenAI)
│   ├── config.py              # Pydantic config models
│   ├── display.py             # Rich display (Live, Tree, Table, logging)
│   ├── rules.py               # Agent rules loading (conditional by subdir)
│   ├── cli.py                 # CLI entry point (run, setup, serve, slack, github, init)
│   ├── web.py                 # FastAPI web UI + GitHub webhook handler
│   ├── tools/
│   │   ├── git_tools.py       # Git operations (branch, commit, push)
│   │   ├── lint_tools.py      # Linting (ruff, eslint) on edited files
│   │   └── pr_tools.py        # PR creation via gh CLI
│   └── integrations/
│       ├── slack_bot.py       # Slack bot (thread parsing, Socket Mode)
│       └── github_client.py   # GitHub API client + webhook handler
├── .minions/config.yaml       # Default configuration
├── .env.example               # Environment variable template
├── AGENTS.md                  # Agent rules for this repo
├── pyproject.toml
└── tests/
```

## Extending

- **MCP servers**: Add tools in `config.yaml` under `mcp.servers`
- **Agent rules**: Drop `.mdc` / `AGENTS.md` into configured paths; rules are conditionally applied by subdirectory
- **GitHub webhooks**: Point your repo's webhook to `/webhooks/github/events`; trigger via issue labels or `/minion` comments

## Troubleshooting

**"No LLM API key found"** — Run `minion setup` or set `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` in your environment or `.env` file.

**GitHub token not detected** — Install the [GitHub CLI](https://cli.github.com/) and run `gh auth login`. Or pass the token via `minion setup`, `GITHUB_TOKEN` env var, or `.env` file.

**Slack tokens** — Slack requires `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`. Set them via `minion setup`, `.env`, or environment variables. See [Slack Bolt docs](https://slack.dev/bolt-python/tutorial/getting-started) for creating a Slack app.

## License

MIT
