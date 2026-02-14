"""Web API and UI for minion runs - with GitHub webhooks and modern UI."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from minions.config import MinionConfig
from minions.orchestrator import Orchestrator

logger = logging.getLogger("minions.web")

app = FastAPI(
    title="Open Minions",
    description="One-shot, end-to-end coding agents",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory run store (use Redis/DB in production)
runs: dict[str, dict[str, Any]] = {}


class RunRequest(BaseModel):
    task: str
    repo_path: str
    links: list[str] | None = None
    ticket_id: str | None = None
    create_pr: bool = False


class RunResponse(BaseModel):
    run_id: str
    status: str
    message: str


def _find_repo_root(start: Path) -> Path | None:
    p = start.resolve()
    for _ in range(20):
        if (p / ".git").exists():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    return None


@app.post("/api/runs", response_model=RunResponse)
async def create_run(req: RunRequest):
    """Start a new minion run."""
    repo = Path(req.repo_path).resolve()
    root = _find_repo_root(repo)
    if not root:
        raise HTTPException(status_code=400, detail="Not a git repository")

    config = MinionConfig.discover(root)
    run_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    runs[run_id] = {
        "id": run_id,
        "task": req.task,
        "repo": str(root),
        "status": "running",
        "created_at": now,
        "state": None,
        "error": None,
        "actions_log": [],
    }

    async def _execute():
        def on_action(a: dict):
            runs[run_id]["actions_log"].append({
                "tool": a.get("tool", "?"),
                "result": str(a.get("result", ""))[:200],
                "ts": datetime.now(timezone.utc).isoformat(),
            })

        try:
            orch = Orchestrator(config, root)
            state = await orch.run(
                task=req.task,
                links=req.links,
                ticket_id=req.ticket_id,
                create_pr_after=req.create_pr,
                github_token=config.github.token_resolved or None,
                on_action=on_action,
            )
            runs[run_id]["state"] = {
                "done": state.done,
                "branch_name": state.branch_name,
                "pr_url": state.pr_url,
                "actions_count": len(state.actions),
                "actions": [
                    {"tool": a["tool"], "result": str(a.get("result", ""))[:200]}
                    for a in state.actions[-15:]
                ],
                "ci_round": state.ci_round,
            }
            runs[run_id]["status"] = "completed" if state.done else "stopped"
        except Exception as e:
            logger.exception("Run %s failed", run_id)
            runs[run_id]["status"] = "failed"
            runs[run_id]["error"] = str(e)

    asyncio.create_task(_execute())

    return RunResponse(
        run_id=run_id,
        status="running",
        message="Minion started. Poll /api/runs/{id} for status.",
    )


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    """Get run status and details."""
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return runs[run_id]


@app.get("/api/runs")
async def list_runs():
    """List all runs."""
    return sorted(runs.values(), key=lambda r: r.get("created_at", ""), reverse=True)


# ── GitHub Webhooks ────────────────────────────────────────
# Mount webhook routes eagerly (signature verification happens at request time)

try:
    from minions.integrations.github_client import build_github_webhook_routes

    _wh_repo = Path(os.environ.get("MINION_REPO_PATH", ".")).resolve()
    _wh_config = MinionConfig.discover(_wh_repo)
    _wh_router = build_github_webhook_routes(_wh_config, str(_wh_repo))
    app.include_router(_wh_router)
except Exception as _wh_err:
    logger.debug("GitHub webhooks not mounted: %s", _wh_err)


# ── Serve UI ───────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "web_static"


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the web UI."""
    html_file = STATIC_DIR / "index.html"
    if html_file.exists():
        return FileResponse(html_file)
    return HTMLResponse(_modern_ui())


def _modern_ui() -> str:
    """Modern embedded UI with Slack/GitHub context."""
    return """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open Minions</title>
  <style>
    :root {
      --bg: #0f1117; --bg2: #1a1d27; --bg3: #252833;
      --fg: #e4e6eb; --fg2: #9ca3af; --accent: #3b82f6;
      --accent2: #60a5fa; --green: #22c55e; --red: #ef4444;
      --yellow: #eab308; --border: #2d3140; --radius: 12px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Inter', system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--fg); min-height: 100vh; }
    .container { max-width: 860px; margin: 0 auto; padding: 2rem 1.5rem; }

    /* Header */
    header { display: flex; align-items: center; gap: 1rem; margin-bottom: 2rem; }
    .logo { font-size: 1.5rem; font-weight: 800; letter-spacing: -0.02em; }
    .logo span { color: var(--accent2); }
    .badge { font-size: 0.7rem; background: var(--bg3); padding: 0.2rem 0.6rem; border-radius: 99px; color: var(--fg2); }

    /* Form */
    .card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.5rem; margin-bottom: 1.5rem; }
    label { display: block; font-size: 0.85rem; font-weight: 600; color: var(--fg2); margin-bottom: 0.4rem; text-transform: uppercase; letter-spacing: 0.05em; }
    textarea, input[type=text] { width: 100%; background: var(--bg3); border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem 1rem; color: var(--fg); font-size: 0.95rem; resize: vertical; transition: border 0.15s; }
    textarea:focus, input:focus { outline: none; border-color: var(--accent); }
    textarea { min-height: 90px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem; }
    .actions { display: flex; gap: 0.75rem; margin-top: 1rem; align-items: center; }
    button { padding: 0.65rem 1.5rem; border-radius: 8px; font-size: 0.9rem; font-weight: 600; cursor: pointer; border: none; transition: all 0.15s; }
    .btn-primary { background: var(--accent); color: white; }
    .btn-primary:hover { background: var(--accent2); transform: translateY(-1px); }
    .btn-secondary { background: var(--bg3); color: var(--fg); border: 1px solid var(--border); }
    .btn-secondary:hover { border-color: var(--accent); }
    .checkbox { display: flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; color: var(--fg2); }
    .checkbox input { accent-color: var(--accent); }

    /* Runs */
    .run { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.25rem; margin-bottom: 1rem; transition: border 0.15s; }
    .run:hover { border-color: var(--accent); }
    .run-header { display: flex; justify-content: space-between; align-items: start; gap: 1rem; }
    .run-task { font-weight: 600; font-size: 0.95rem; flex: 1; }
    .status-badge { font-size: 0.75rem; font-weight: 700; padding: 0.2rem 0.7rem; border-radius: 99px; text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap; }
    .status-running { background: rgba(59,130,246,0.15); color: var(--accent2); }
    .status-completed { background: rgba(34,197,94,0.15); color: var(--green); }
    .status-failed { background: rgba(239,68,68,0.15); color: var(--red); }
    .status-stopped { background: rgba(234,179,8,0.15); color: var(--yellow); }
    .run-meta { margin-top: 0.75rem; font-size: 0.8rem; color: var(--fg2); display: flex; flex-wrap: wrap; gap: 1.25rem; }
    .run-meta a { color: var(--accent2); text-decoration: none; }
    .run-meta a:hover { text-decoration: underline; }
    .actions-log { margin-top: 0.75rem; max-height: 200px; overflow-y: auto; font-size: 0.8rem; font-family: 'JetBrains Mono', 'Fira Code', monospace; background: var(--bg); border-radius: 8px; padding: 0.75rem; border: 1px solid var(--border); }
    .action-line { padding: 0.15rem 0; color: var(--fg2); }
    .action-line .tool { color: var(--accent2); font-weight: 600; }
    .empty { text-align: center; color: var(--fg2); padding: 3rem 0; }

    @media (max-width: 600px) { .row { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="logo">OPEN<span>MINIONS</span></div>
      <div class="badge">one-shot coding agents</div>
    </header>

    <div class="card">
      <form id="runForm">
        <label>Task</label>
        <textarea id="task" placeholder="e.g. Add retry logic to the API client in src/api/client.py with exponential backoff" required></textarea>
        <div class="row">
          <div>
            <label>Repo Path</label>
            <input id="repo" type="text" placeholder="/path/to/your/repo" value="." />
          </div>
          <div>
            <label>Links (optional)</label>
            <input id="links" type="text" placeholder="https://docs.example.com, https://..." />
          </div>
        </div>
        <div class="actions">
          <button type="submit" class="btn-primary">Start Minion</button>
          <label class="checkbox">
            <input type="checkbox" id="createPr" /> Create PR when done
          </label>
        </div>
      </form>
    </div>

    <div id="runs"></div>
    <div id="empty" class="empty">No runs yet. Start a minion above.</div>
  </div>

  <script>
    const form = document.getElementById('runForm');
    const runsEl = document.getElementById('runs');
    const emptyEl = document.getElementById('empty');
    const polls = {};

    // Load existing runs
    fetch('/api/runs').then(r=>r.json()).then(data => {
      if (data.length) emptyEl.style.display = 'none';
      data.forEach(d => { renderRun(d); if (d.status === 'running') startPoll(d.id); });
    });

    form.onsubmit = async (e) => {
      e.preventDefault();
      const task = document.getElementById('task').value;
      const repo = document.getElementById('repo').value || '.';
      const links = document.getElementById('links').value.split(',').map(s=>s.trim()).filter(Boolean);
      const createPr = document.getElementById('createPr').checked;
      const res = await fetch('/api/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task, repo_path: repo, links: links.length ? links : null, create_pr: createPr })
      });
      const data = await res.json();
      emptyEl.style.display = 'none';
      renderRun({ id: data.run_id, task, status: 'running', actions_log: [] });
      startPoll(data.run_id);
      form.reset();
    };

    function startPoll(id) {
      polls[id] = setInterval(async () => {
        const r = await fetch('/api/runs/' + id);
        const d = await r.json();
        renderRun(d);
        if (d.status !== 'running') { clearInterval(polls[id]); delete polls[id]; }
      }, 2000);
    }

    function renderRun(d) {
      let el = document.getElementById('run-' + d.id);
      if (!el) {
        el = document.createElement('div');
        el.className = 'run';
        el.id = 'run-' + d.id;
        runsEl.insertBefore(el, runsEl.firstChild);
      }
      const s = d.state || {};
      let metaParts = [];
      if (s.branch_name) metaParts.push('Branch: <code>' + s.branch_name + '</code>');
      if (s.pr_url) metaParts.push('PR: <a href="' + s.pr_url + '" target="_blank">' + s.pr_url + '</a>');
      if (s.actions_count) metaParts.push(s.actions_count + ' actions');
      if (s.ci_round) metaParts.push('CI round: ' + s.ci_round);
      if (d.error) metaParts.push('<span style="color:var(--red)">' + d.error.slice(0,120) + '</span>');

      let actionsHtml = '';
      const log = d.actions_log || (s.actions || []);
      if (log.length) {
        actionsHtml = '<div class="actions-log">' +
          log.slice(-8).map(a =>
            '<div class="action-line"><span class="tool">' + (a.tool||'?') + '</span> ' + (a.result||'').slice(0,100) + '</div>'
          ).join('') + '</div>';
      }

      el.innerHTML =
        '<div class="run-header">' +
          '<div class="run-task">' + (d.task||'').slice(0,100) + '</div>' +
          '<span class="status-badge status-' + d.status + '">' + d.status + '</span>' +
        '</div>' +
        (metaParts.length ? '<div class="run-meta">' + metaParts.join(' &middot; ') + '</div>' : '') +
        actionsHtml;
    }
  </script>
</body>
</html>"""


# Mount static if it exists
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
