from __future__ import annotations

from typing import Any, Dict, Iterator

import ollama as _ollama


class OllamaClientError(RuntimeError):
    """Raised when an Ollama request fails."""


# Backward-compat aliases
ApexClientError = OllamaClientError


class OllamaClient:
    """Local Ollama-backed AI client. Requires Ollama running at `host`."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "llama3.2",
        timeout_s: float = 120.0,
    ) -> None:
        self.host = host
        self.model = model
        self.timeout_s = timeout_s
        self._client = _ollama.Client(host=host)

    def stream_query(
        self,
        *,
        prompt: str,
        context: Dict[str, Any] | None = None,
        model: str | None = None,
        system_prompt: str = "You are a helpful, professional AI assistant for nonprofit organizations.",
        # Legacy params accepted but unused by Ollama
        tenant_id: str = "ngo-default",
        session_id: str = "default",
    ) -> Iterator[str]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        use_model = model or self.model
        try:
            for chunk in self._client.chat(model=use_model, messages=messages, stream=True):
                # ollama library returns ChatResponse objects or dicts
                if isinstance(chunk, dict):
                    content = chunk.get("message", {}).get("content", "")
                else:
                    msg = getattr(chunk, "message", None)
                    content = getattr(msg, "content", "") if msg is not None else ""
                if content:
                    yield content
        except Exception as exc:
            raise OllamaClientError(f"Ollama request failed: {exc}") from exc

    def query(self, **kwargs: Any) -> str:
        return "".join(self.stream_query(**kwargs))


# Backward-compat alias
ApexClient = OllamaClient
