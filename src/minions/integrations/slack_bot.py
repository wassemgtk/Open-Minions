"""
Slack integration for Open Minions.

Engineers invoke a minion by @-mentioning the bot in a Slack thread.
The bot:
  1. Reads the full thread (messages + links) as context
  2. Kicks off a minion run
  3. Posts live status updates back into the thread
  4. Posts the PR link when done
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from minions.config import MinionConfig
from minions.orchestrator import Orchestrator, RunState

logger = logging.getLogger("minions.slack")

# URL regex for extracting links from Slack messages
_URL_RE = re.compile(r"<(https?://[^>|]+)(?:\|[^>]*)?>")
_SLACK_LINK_RE = re.compile(r"<([^>]+)>")


@dataclass
class SlackContext:
    """Parsed context from a Slack thread."""

    task: str
    thread_messages: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    channel_id: str = ""
    thread_ts: str = ""
    user_id: str = ""


def parse_thread_context(
    messages: list[dict[str, Any]],
    bot_user_id: str,
) -> SlackContext:
    """
    Parse a Slack thread into a SlackContext.
    The message that @-mentions the bot is treated as the task.
    Prior messages become thread context. All URLs are extracted.
    """
    ctx = SlackContext(task="")
    thread_texts: list[str] = []
    links: set[str] = set()

    for msg in messages:
        text = msg.get("text", "")

        # Extract all URLs
        for url_match in _URL_RE.finditer(text):
            links.add(url_match.group(1))

        # Check if this message mentions the bot
        if f"<@{bot_user_id}>" in text:
            # Strip the bot mention to get the task
            task_text = text.replace(f"<@{bot_user_id}>", "").strip()
            # Clean up Slack formatting
            task_text = _clean_slack_text(task_text)
            ctx.task = task_text
            ctx.user_id = msg.get("user", "")
        else:
            clean = _clean_slack_text(text)
            if clean:
                thread_texts.append(clean)

    ctx.thread_messages = thread_texts
    ctx.links = sorted(links)
    return ctx


def _clean_slack_text(text: str) -> str:
    """Clean Slack mrkdwn formatting into plain text."""
    # Replace Slack-style links <url|label> with just label or url
    text = re.sub(r"<(https?://[^>|]+)\|([^>]+)>", r"\2 (\1)", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    # Strip user/channel mentions to readable form
    text = re.sub(r"<@(\w+)>", r"@\1", text)
    text = re.sub(r"<#(\w+)\|([^>]+)>", r"#\2", text)
    # Strip paired bold/italic/strikethrough markers (not bare characters)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)  # *bold*
    text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", text)  # _italic_ (word-boundary safe)
    text = re.sub(r"~([^~]+)~", r"\1", text)  # ~strikethrough~
    return text.strip()


def build_task_with_thread_context(ctx: SlackContext) -> str:
    """Build a full task prompt from Slack context."""
    parts = [ctx.task]
    if ctx.thread_messages:
        parts.append("\n\n## Thread context (prior messages):\n")
        for i, msg in enumerate(ctx.thread_messages, 1):
            parts.append(f"{i}. {msg}")
    return "\n".join(parts)


class SlackBot:
    """
    Slack bot that listens for @-mentions and runs minions.

    Uses Slack's Bolt framework (slack_bolt) with Socket Mode for
    real-time event handling without exposing a public endpoint.
    """

    def __init__(
        self,
        slack_bot_token: str,
        slack_app_token: str,
        default_repo_path: str,
        config: MinionConfig | None = None,
    ):
        self.slack_bot_token = slack_bot_token
        self.slack_app_token = slack_app_token
        self.default_repo_path = default_repo_path
        self.config = config or MinionConfig()
        self._app = None
        self._bot_user_id: str | None = None

    def _ensure_bolt(self):
        """Lazy-import slack_bolt to keep it optional."""
        try:
            from slack_bolt.async_app import AsyncApp
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        except ImportError:
            raise ImportError(
                "Slack integration requires slack_bolt. "
                "Install with: pip install 'open-minions[slack]'"
            )
        return AsyncApp, AsyncSocketModeHandler

    def _create_app(self):
        AsyncApp, _ = self._ensure_bolt()
        self._app = AsyncApp(token=self.slack_bot_token)
        self._register_handlers()
        return self._app

    def _register_handlers(self):
        app = self._app

        @app.event("app_mention")
        async def handle_mention(event, say, client):
            channel = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            user = event.get("user", "")

            # Acknowledge immediately
            await say(
                text=":robot_face: Minion activated! Reading thread context...",
                thread_ts=thread_ts,
            )

            # Fetch thread messages for context
            messages = []
            try:
                result = await client.conversations_replies(
                    channel=channel,
                    ts=thread_ts,
                    limit=50,
                )
                messages = result.get("messages", [])
            except Exception as e:
                logger.warning(f"Failed to fetch thread: {e}")
                messages = [event]

            # Get bot user ID
            if not self._bot_user_id:
                try:
                    auth = await client.auth_test()
                    self._bot_user_id = auth["user_id"]
                except Exception:
                    self._bot_user_id = "UNKNOWN"

            # Parse thread into context
            ctx = parse_thread_context(messages, self._bot_user_id)
            ctx.channel_id = channel
            ctx.thread_ts = thread_ts

            if not ctx.task:
                await say(
                    text=":warning: I couldn't find a task in your message. "
                    "Please @-mention me with a description of what you'd like done.",
                    thread_ts=thread_ts,
                )
                return

            task = build_task_with_thread_context(ctx)

            # Post "working" status
            status_msg = await say(
                text=f":hammer_and_wrench: Working on: _{ctx.task[:100]}_\n"
                f"Context: {len(ctx.thread_messages)} thread messages, "
                f"{len(ctx.links)} links found.",
                thread_ts=thread_ts,
            )

            # Run the minion
            try:
                from pathlib import Path

                repo = Path(self.default_repo_path).resolve()
                orch = Orchestrator(self.config, repo)
                state = await orch.run(
                    task=task,
                    links=ctx.links or None,
                    create_pr_after=True,
                )

                # Post result
                if state.done and state.branch_name:
                    pr_action = next(
                        (a for a in state.actions if a.get("tool") == "create_pr"),
                        None,
                    )
                    pr_url = pr_action.get("result") if pr_action else None

                    result_text = (
                        f":white_check_mark: Minion completed!\n"
                        f"*Branch:* `{state.branch_name}`\n"
                        f"*Actions:* {len(state.actions)} steps"
                    )
                    if pr_url:
                        result_text += f"\n*PR:* {pr_url}"
                    else:
                        result_text += (
                            "\n_PR not created automatically. "
                            "Push and open one manually, or set up `gh` CLI._"
                        )

                    await say(text=result_text, thread_ts=thread_ts)
                elif state.done:
                    await say(
                        text=":large_yellow_circle: Minion finished but made no changes.",
                        thread_ts=thread_ts,
                    )
                else:
                    await say(
                        text=":warning: Minion stopped without completing. "
                        "Check logs for details.",
                        thread_ts=thread_ts,
                    )
            except Exception as e:
                logger.exception("Minion run failed")
                await say(
                    text=f":x: Minion failed: {e!s:.200}",
                    thread_ts=thread_ts,
                )

        @app.event("message")
        async def handle_message(event, say):
            """Ignore regular messages (required to avoid warnings)."""
            pass

    async def start(self):
        """Start the Slack bot with Socket Mode."""
        _, AsyncSocketModeHandler = self._ensure_bolt()
        app = self._create_app()
        handler = AsyncSocketModeHandler(app, self.slack_app_token)
        logger.info("Starting Slack bot in Socket Mode...")
        await handler.start_async()

    def run_sync(self):
        """Run the Slack bot synchronously (blocking)."""
        asyncio.run(self.start())
