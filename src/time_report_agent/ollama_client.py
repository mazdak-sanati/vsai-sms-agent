"""Minimal Ollama chat client with tool calling and progress traces."""

from __future__ import annotations

import sys
import time
from typing import Any, Callable

import httpx


class OllamaError(RuntimeError):
    """Raised when Ollama cannot complete a chat turn."""


ProgressFn = Callable[[str], None]


def _default_progress(message: str) -> None:
    print(message, flush=True)


class OllamaClient:
    def __init__(
        self,
        *,
        host: str,
        model: str,
        timeout_seconds: float = 300.0,
        think: bool = False,
        progress: ProgressFn | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.think = think
        self.progress = progress or _default_progress

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            # gemma4 defaults to thinking=true which looks like a hang in the terminal.
            "think": self.think,
            "options": {"temperature": 0.2},
        }
        if tools:
            payload["tools"] = tools

        self.progress(
            f"[ollama] requesting model={self.model} think={self.think} "
            f"messages={len(messages)} tools={0 if not tools else len(tools)} ..."
        )
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(f"{self.host}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            elapsed = time.perf_counter() - started
            raise OllamaError(
                f"Ollama timed out after {elapsed:.1f}s "
                f"(limit {self.timeout_seconds:.0f}s): {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            elapsed = time.perf_counter() - started
            raise OllamaError(
                f"Ollama request failed after {elapsed:.1f}s: {exc}"
            ) from exc

        elapsed = time.perf_counter() - started
        message = data.get("message")
        if not isinstance(message, dict):
            raise OllamaError("Ollama response missing message object")

        tool_calls = message.get("tool_calls") or []
        content = (message.get("content") or "").strip()
        thinking = (message.get("thinking") or "").strip()
        self.progress(
            f"[ollama] done in {elapsed:.1f}s "
            f"content_chars={len(content)} "
            f"thinking_chars={len(thinking)} "
            f"tool_calls={len(tool_calls) if isinstance(tool_calls, list) else 0}"
        )
        if thinking and self.think:
            preview = thinking.replace("\n", " ")
            if len(preview) > 180:
                preview = preview[:177] + "..."
            self.progress(f"[ollama] thinking: {preview}")
        return message

    def ping(self) -> None:
        """Fail fast if Ollama is unreachable or the model tag is missing."""
        self.progress(f"[ollama] checking {self.host} for model {self.model} ...")
        try:
            with httpx.Client(timeout=15.0) as client:
                tags = client.get(f"{self.host}/api/tags")
                tags.raise_for_status()
                models = {
                    str(item.get("name") or "")
                    for item in (tags.json().get("models") or [])
                    if isinstance(item, dict)
                }
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host}: {exc}. "
                "Start it with: ollama serve"
            ) from exc

        exact = self.model in models or f"{self.model}:latest" in models
        family = self.model.split(":")[0]
        family_hit = any(name == family or name.startswith(f"{family}:") for name in models)
        if not exact and not family_hit:
            raise OllamaError(
                f"Model {self.model!r} not found in Ollama. Run: ollama pull {self.model}"
            )
        if not exact:
            self.progress(
                f"[ollama] warning: exact tag {self.model!r} not listed; "
                f"found related tags: {sorted(n for n in models if n.startswith(family))}"
            )
        self.progress("[ollama] ready (first reply may take longer while the model loads)")