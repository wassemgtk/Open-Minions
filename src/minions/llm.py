"""LLM provider abstraction for the agent."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from minions.config import LLMConfig


class LLMClient:
    """Unified LLM client supporting Anthropic and OpenAI with caching and fallback."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._anthropic = None
        self._openai = None

    def _get_anthropic(self):
        if self._anthropic is None:
            import anthropic

            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise ValueError("ANTHROPIC_API_KEY not set")
            self._anthropic = anthropic.AsyncAnthropic(api_key=key)
        return self._anthropic

    def _get_openai(self):
        if self._openai is None:
            from openai import AsyncOpenAI

            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise ValueError("OPENAI_API_KEY not set")
            self._openai = AsyncOpenAI(api_key=key)
        return self._openai

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.2,
    ) -> str:
        """Complete a chat and return the text response."""
        providers = [(self.config.provider, self.config.model)]
        if self.config.fallback_provider and self.config.fallback_model:
            providers.append((self.config.fallback_provider, self.config.fallback_model))

        last_error = None
        for provider, model in providers:
            try:
                if provider == "anthropic":
                    return await self._complete_anthropic(
                        model, messages, system, max_tokens, temperature
                    )
                elif provider == "openai":
                    return await self._complete_openai(
                        model, messages, system, max_tokens, temperature
                    )
                else:
                    raise ValueError(f"Unknown provider: {provider}")
            except Exception as e:
                last_error = e
                continue
        raise last_error or RuntimeError("No LLM provider available")

    async def _complete_anthropic(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> str:
        client = self._get_anthropic()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        r = await client.messages.create(**kwargs)
        return (r.content[0].text if r.content else "") or ""

    async def _complete_openai(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> str:
        client = self._get_openai()
        msgs = [{"role": "system", "content": system or "You are a helpful coding assistant."}]
        msgs.extend(messages)
        r = await client.chat.completions.create(
            model=model,
            messages=msgs,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return (r.choices[0].message.content or "") or ""

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 8192,
    ) -> AsyncIterator[str]:
        """Stream a chat completion. Dispatches to configured provider with fallback."""
        providers = [(self.config.provider, self.config.model)]
        if self.config.fallback_provider and self.config.fallback_model:
            providers.append((self.config.fallback_provider, self.config.fallback_model))

        for provider, model in providers:
            try:
                if provider == "anthropic":
                    async for chunk in self._stream_anthropic(model, messages, system, max_tokens):
                        yield chunk
                    return
                elif provider == "openai":
                    async for chunk in self._stream_openai(model, messages, system, max_tokens):
                        yield chunk
                    return
            except Exception:
                continue
        raise RuntimeError("No LLM provider available for streaming")

    async def _stream_anthropic(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        client = self._get_anthropic()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        async with client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text

    async def _stream_openai(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        client = self._get_openai()
        msgs = [{"role": "system", "content": system or "You are a helpful coding assistant."}]
        msgs.extend(messages)
        stream = await client.chat.completions.create(
            model=model,
            messages=msgs,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
