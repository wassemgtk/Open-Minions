"""
GitHub integration for Open Minions.

Full GitHub API support via httpx (no heavy PyGithub dep):
  - Create/manage PRs with full metadata
  - Read issues and comments for context hydration
  - Check CI status and parse failures
  - Post review comments
  - Webhook handler for GitHub Events (issue assigned, PR review requested, etc.)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("minions.github")

_API = "https://api.github.com"


@dataclass
class PRInfo:
    """Pull request information."""

    url: str
    number: int
    html_url: str
    state: str
    title: str
    branch: str
    base: str


@dataclass
class IssueInfo:
    """Issue information for context."""

    number: int
    title: str
    body: str
    labels: list[str]
    html_url: str
    comments: list[dict[str, str]]


@dataclass
class CheckStatus:
    """CI check status for a ref."""

    state: str  # success | failure | pending
    total: int
    passed: int
    failed: int
    failures: list[dict[str, str]]  # name, output


class GitHubClient:
    """Async GitHub API client using httpx."""

    def __init__(self, token: str, owner: str = "", repo: str = ""):
        self.token = token
        self.owner = owner
        self.repo = repo
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_remote_url(cls, token: str, remote_url: str) -> "GitHubClient":
        """Create client from a git remote URL."""
        import re

        # Match git@github.com:owner/repo.git or https://github.com/owner/repo.git
        m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", remote_url)
        if not m:
            raise ValueError(f"Cannot parse GitHub owner/repo from: {remote_url}")
        return cls(token=token, owner=m.group(1), repo=m.group(2))

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=_API,
                headers=self._headers,
                timeout=30.0,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Pull Requests ──────────────────────────────────────────

    async def create_pull_request(
        self,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
        draft: bool = False,
    ) -> PRInfo:
        """Create a pull request."""
        client = await self._ensure_client()
        resp = await client.post(
            f"/repos/{self.owner}/{self.repo}/pulls",
            json={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
                "draft": draft,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return PRInfo(
            url=data["url"],
            number=data["number"],
            html_url=data["html_url"],
            state=data["state"],
            title=data["title"],
            branch=head,
            base=base,
        )

    async def update_pull_request(
        self,
        pr_number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Update a PR's title, body, or state."""
        client = await self._ensure_client()
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        resp = await client.patch(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    async def add_pr_comment(self, pr_number: int, body: str) -> dict[str, Any]:
        """Add a comment to a PR."""
        client = await self._ensure_client()
        resp = await client.post(
            f"/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_pull_request(self, pr_number: int) -> PRInfo:
        """Get PR details."""
        client = await self._ensure_client()
        resp = await client.get(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        )
        resp.raise_for_status()
        data = resp.json()
        return PRInfo(
            url=data["url"],
            number=data["number"],
            html_url=data["html_url"],
            state=data["state"],
            title=data["title"],
            branch=data["head"]["ref"],
            base=data["base"]["ref"],
        )

    async def list_pr_files(self, pr_number: int) -> list[dict[str, Any]]:
        """List files changed in a PR."""
        client = await self._ensure_client()
        resp = await client.get(
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/files",
            params={"per_page": 100},
        )
        resp.raise_for_status()
        return resp.json()

    # ── Issues ─────────────────────────────────────────────────

    async def get_issue(self, issue_number: int, include_comments: bool = True) -> IssueInfo:
        """Get issue details with optional comments (for context hydration)."""
        client = await self._ensure_client()
        resp = await client.get(
            f"/repos/{self.owner}/{self.repo}/issues/{issue_number}"
        )
        resp.raise_for_status()
        data = resp.json()

        comments: list[dict[str, str]] = []
        if include_comments and data.get("comments", 0) > 0:
            resp_c = await client.get(
                f"/repos/{self.owner}/{self.repo}/issues/{issue_number}/comments",
                params={"per_page": 30},
            )
            if resp_c.status_code == 200:
                for c in resp_c.json():
                    comments.append({
                        "user": c.get("user", {}).get("login", ""),
                        "body": c.get("body", ""),
                    })

        return IssueInfo(
            number=data["number"],
            title=data["title"],
            body=data.get("body", "") or "",
            labels=[lb.get("name", "") for lb in data.get("labels", [])],
            html_url=data["html_url"],
            comments=comments,
        )

    async def create_issue_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        """Post a comment on an issue."""
        client = await self._ensure_client()
        resp = await client.post(
            f"/repos/{self.owner}/{self.repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()

    # ── Checks / CI ────────────────────────────────────────────

    async def get_check_status(self, ref: str) -> CheckStatus:
        """Get combined check status for a ref (branch or SHA)."""
        client = await self._ensure_client()

        # Check runs (GitHub Actions, etc.)
        resp = await client.get(
            f"/repos/{self.owner}/{self.repo}/commits/{ref}/check-runs",
            params={"per_page": 100},
        )
        resp.raise_for_status()
        data = resp.json()

        total = data.get("total_count", 0)
        runs = data.get("check_runs", [])
        passed = sum(1 for r in runs if r.get("conclusion") == "success")
        failed_runs = [r for r in runs if r.get("conclusion") == "failure"]

        failures = []
        for r in failed_runs:
            failures.append({
                "name": r.get("name", "unknown"),
                "output": (r.get("output", {}) or {}).get("summary", "")[:500],
                "details_url": r.get("details_url", ""),
            })

        if failed_runs:
            state = "failure"
        elif passed == total and total > 0:
            state = "success"
        else:
            state = "pending"

        return CheckStatus(
            state=state,
            total=total,
            passed=passed,
            failed=len(failed_runs),
            failures=failures,
        )

    async def wait_for_checks(
        self,
        ref: str,
        timeout_seconds: int = 600,
        poll_interval: int = 30,
    ) -> CheckStatus:
        """Poll check status until complete or timeout."""
        import asyncio

        elapsed = 0
        while elapsed < timeout_seconds:
            status = await self.get_check_status(ref)
            if status.state in ("success", "failure"):
                return status
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        return await self.get_check_status(ref)

    # ── Repository ─────────────────────────────────────────────

    async def get_file_content(self, path: str, ref: str = "main") -> str:
        """Get file content from the repo (for context)."""
        client = await self._ensure_client()
        resp = await client.get(
            f"/repos/{self.owner}/{self.repo}/contents/{path}",
            params={"ref": ref},
            headers={**self._headers, "Accept": "application/vnd.github.raw+json"},
        )
        resp.raise_for_status()
        return resp.text

    async def search_code(self, query: str, per_page: int = 10) -> list[dict[str, Any]]:
        """Search code in the repo."""
        client = await self._ensure_client()
        resp = await client.get(
            "/search/code",
            params={
                "q": f"{query} repo:{self.owner}/{self.repo}",
                "per_page": per_page,
            },
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [
            {
                "path": item.get("path", ""),
                "html_url": item.get("html_url", ""),
                "score": item.get("score", 0),
            }
            for item in items
        ]


# ── Webhook handler (FastAPI integration) ──────────────────


def build_github_webhook_routes(config: "MinionConfig", default_repo_path: str):
    """
    Create FastAPI router for GitHub webhook events.
    Use: app.include_router(build_github_webhook_routes(config, repo_path))
    """
    import asyncio
    import hashlib
    import hmac
    import os

    from fastapi import APIRouter, Header, HTTPException, Request

    router = APIRouter(prefix="/webhooks/github", tags=["github"])

    @router.post("/events")
    async def github_webhook(
        request: Request,
        x_github_event: str = Header(default=""),
        x_hub_signature_256: str = Header(default=""),
    ):
        """Handle GitHub webhook events to auto-trigger minion runs."""
        body = await request.body()

        # Verify signature if webhook secret is configured
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
        if secret:
            expected = "sha256=" + hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, x_hub_signature_256):
                raise HTTPException(status_code=401, detail="Invalid signature")

        payload = await request.json()
        event_type = x_github_event

        if event_type == "issues" and payload.get("action") == "labeled":
            label = payload.get("label", {}).get("name", "")
            if label == "minion":
                issue = payload.get("issue", {})
                task = f"Fix issue #{issue['number']}: {issue['title']}\n\n{issue.get('body', '')}"
                logger.info("Minion triggered by issue label: #%s", issue.get("number"))
                asyncio.create_task(_run_from_webhook(config, default_repo_path, task))

        elif event_type == "issue_comment":
            comment_body = payload.get("comment", {}).get("body", "")
            if comment_body.strip().lower().startswith("/minion"):
                task_text = comment_body.replace("/minion", "", 1).strip()
                issue = payload.get("issue", {})
                task = (
                    f"From issue #{issue['number']} ({issue['title']}):\n\n"
                    f"{task_text or issue.get('body', '')}"
                )
                logger.info("Minion triggered by /minion comment on #%s", issue.get("number"))
                asyncio.create_task(_run_from_webhook(config, default_repo_path, task))

        return {"status": "ok", "event": event_type}

    async def _run_from_webhook(cfg: "MinionConfig", repo_path: str, task: str):
        try:
            from minions.orchestrator import Orchestrator

            orch = Orchestrator(cfg, Path(repo_path))
            await orch.run(task=task, create_pr_after=True)
        except Exception:
            logger.exception("Webhook-triggered minion failed")

    return router
