# Open Minions

**One-shot, end-to-end coding agents.** Unattended agents that turn a task into a pull request -- no human interaction in between.

Inspired by [Stripe's Minions](https://stripe.com/blog/minions) -- homegrown coding agents that merge over a thousand PRs weekly at Stripe with no human-written code.

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

### Prerequisites

- Python 3.11+
- Git
- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (or both for fallback)

### Install

```bash
cd open-minions
pip install -e .

# With Slack support:
pip install -e '.[slack]'
```

### Run a Minion

```bash
# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run a task in current repo
minion run "Add a retry decorator to the fetch_user function in api/client.py"

# With explicit repo, links, and auto-PR creation
minion run "Fix the type error in utils/validators.py" \
  --repo ~/my-project \
  --links "https://docs.example.com/validators" \
  --create-pr

# Initialize config in a repo
minion init --repo ~/my-project
```

### Web UI

```bash
minion serve
# -> http://localhost:8080
```

Create runs, view live action logs, and manage minion output from the browser.

### Slack Bot

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
minion slack --repo ~/my-project
```

Engineers @-mention the bot in a Slack thread. The bot reads the full thread (messages + links), runs the minion, and posts the PR link back.

### GitHub CLI

```bash
export GITHUB_TOKEN=ghp_...

# View an issue with comments
minion github issue 42

# Check PR status
minion github pr-status 100

# View CI check results
minion github checks 100
```

### GitHub Webhooks

The web server mounts a webhook handler at `/webhooks/github/events`. Set up a GitHub webhook pointing to your server. Minion runs are triggered by:

- Labeling an issue with `minion`
- Commenting `/minion <task>` on an issue

## Configuration

Create `.minions/config.yaml` via `minion init`, or set environment variables:

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
│   ├── cli.py                 # CLI entry point (run, serve, slack, github, init)
│   ├── web.py                 # FastAPI web UI + GitHub webhook handler
│   ├── tools/
│   │   ├── git_tools.py       # Git operations (branch, commit, push)
│   │   ├── lint_tools.py      # Linting (ruff, eslint) on edited files
│   │   └── pr_tools.py        # PR creation via gh CLI
│   └── integrations/
│       ├── slack_bot.py       # Slack bot (thread parsing, Socket Mode)
│       └── github_client.py   # GitHub API client + webhook handler
├── .minions/config.yaml       # Default configuration
├── AGENTS.md                  # Agent rules for this repo
├── pyproject.toml
└── tests/
```

## Extending

- **MCP servers**: Add tools in `config.yaml` under `mcp.servers`
- **Agent rules**: Drop `.mdc` / `AGENTS.md` into configured paths; rules are conditionally applied by subdirectory
- **GitHub webhooks**: Point your repo's webhook to `/webhooks/github/events`; trigger via issue labels or `/minion` comments

## License

MIT
