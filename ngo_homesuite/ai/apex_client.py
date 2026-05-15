from __future__ import annotations

import json
from typing import Any, Dict, Iterator

import requests


class ApexClientError(RuntimeError):
    """Raised when an Apex Sovereign request fails."""


class ApexClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_token: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout_s = timeout_s

    def _headers(self, tenant_id: str, session_id: str) -> Dict[str, str]:
        headers = {
            "x-tenant-id": tenant_id,
            "x-session-id": session_id,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    @staticmethod
    def _extract_text(payload: Dict[str, Any]) -> str:
        if isinstance(payload.get("content"), str):
            return payload["content"]

        delta = payload.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            return delta["content"]

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                choice_delta = first.get("delta")
                if isinstance(choice_delta, dict) and isinstance(choice_delta.get("content"), str):
                    return choice_delta["content"]
                if isinstance(first.get("message"), dict) and isinstance(first["message"].get("content"), str):
                    return first["message"]["content"]

        text = payload.get("text")
        return text if isinstance(text, str) else ""

    def stream_query(
        self,
        *,
        prompt: str,
        context: Dict[str, Any] | None = None,
        model: str = "llama3.2",
        tenant_id: str = "ngo-default",
        session_id: str = "default",
        system_prompt: str = "You are a helpful, professional AI assistant for nonprofit organizations.",
    ) -> Iterator[str]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "metadata": {
                "context": context or {},
            },
        }

        url = f"{self.base_url}/v1/stream"
        try:
            with requests.post(
                url,
                headers=self._headers(tenant_id=tenant_id, session_id=session_id),
                json=payload,
                timeout=self.timeout_s,
                stream=True,
            ) as response:
                response.raise_for_status()
                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue

                    line = raw_line.strip()
                    if line.startswith("data:"):
                        line = line[5:].strip()

                    if line == "[DONE]":
                        break

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    token = self._extract_text(data)
                    if token:
                        yield token
        except requests.RequestException as exc:
            raise ApexClientError(f"Apex request failed: {exc}") from exc

    def query(self, **kwargs: Any) -> str:
        return "".join(self.stream_query(**kwargs))
