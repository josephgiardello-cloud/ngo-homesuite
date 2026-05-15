from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import current_app
import ollama

from ngo_homesuite.ai.copilot_tools import CopilotToolRegistry
from ngo_homesuite.ai.pii_redact import redact_pii
from ngo_homesuite.ai.rag_index import LocalRAGIndex
from ngo_homesuite.prompts import NGO_APEX_POLICY_SYSTEM_PROMPT


COPILOT_SYSTEM_PROMPT = (
    "You are HomeSuite Copilot, an expert assistant for this exact nonprofit management system. "
    "You help users run workflows, troubleshoot, and answer product questions accurately. "
    "When tool outputs or retrieved sources are available, prefer them over assumptions. "
    "Never reveal secrets or unredacted PII."
)


@dataclass
class CopilotResponse:
    answer: str
    sources: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    redactions: int


class HomeSuiteCopilot:
    def __init__(
        self,
        host: str,
        model: str,
        embed_model: str,
        index_dir: str,
        project_root: str,
        rag_k: int = 6,
    ) -> None:
        self.client = ollama.Client(host=host)
        self.model = model
        self.rag_k = max(1, int(rag_k))
        self.project_root = project_root
        self.index = LocalRAGIndex(index_dir=index_dir, embed_model=embed_model)
        self.tools = CopilotToolRegistry()

    @classmethod
    def from_app(cls) -> "HomeSuiteCopilot":
        app = current_app
        project_root = str(Path(app.root_path).parent)
        return cls(
            host=app.config.get("OLLAMA_HOST", "http://localhost:11434"),
            model=app.config.get("OLLAMA_MODEL", "llama3.2"),
            embed_model=app.config.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
            index_dir=app.config.get("COPILOT_INDEX_DIR", "data/copilot_index"),
            project_root=project_root,
            rag_k=app.config.get("COPILOT_RAG_K", 6),
        )

    def reindex(self, user_summary_texts: list[str] | None = None) -> int:
        return self.index.build(self.project_root, user_summary_texts=user_summary_texts)

    def _build_runtime_context_text(self, context: dict[str, Any]) -> str:
        if not context:
            return ""
        keys = [
            "active_page",
            "page",
            "organization",
            "donor_count",
            "donation_count",
            "expense_count",
            "project_count",
            "fund_count",
            "total_donations",
            "total_expenses",
            "net_balance",
        ]
        pairs = [f"{k}={context.get(k)}" for k in keys if context.get(k) is not None]
        if not pairs:
            return ""
        return "Current app context: " + ", ".join(pairs)

    def _messages_with_retrieval(self, prompt: str, context: dict[str, Any], sources: list[dict[str, Any]]):
        source_lines = []
        for i, src in enumerate(sources, start=1):
            snippet = (src.get("text") or "").strip().replace("\n", " ")
            if len(snippet) > 600:
                snippet = snippet[:600] + "..."
            source_lines.append(f"[{i}] {src.get('source')}: {snippet}")

        retrieval_block = "\n".join(source_lines) if source_lines else "No retrieval matches found."
        runtime_ctx = self._build_runtime_context_text(context)

        user_payload = "\n\n".join(
            part for part in [runtime_ctx, f"Retrieved knowledge:\n{retrieval_block}", f"User request: {prompt}"] if part
        )

        return [
            {
                "role": "system",
                "content": f"{COPILOT_SYSTEM_PROMPT}\n\n{NGO_APEX_POLICY_SYSTEM_PROMPT}",
            },
            {"role": "user", "content": user_payload},
        ]

    def _extract_tool_calls(self, resp: Any) -> list[dict[str, Any]]:
        if isinstance(resp, dict):
            return resp.get("message", {}).get("tool_calls", []) or []
        msg = getattr(resp, "message", None)
        if msg is None:
            return []
        calls = getattr(msg, "tool_calls", None)
        return list(calls or [])

    def _extract_content(self, resp: Any) -> str:
        if isinstance(resp, dict):
            return str(resp.get("message", {}).get("content", "") or "")
        msg = getattr(resp, "message", None)
        return str(getattr(msg, "content", "") or "")

    def _tool_call_parts(self, call: Any) -> tuple[str, dict[str, Any]]:
        if isinstance(call, dict):
            fn = call.get("function", {})
            name = fn.get("name", "")
            arguments = fn.get("arguments", {})
        else:
            fn = getattr(call, "function", None)
            name = getattr(fn, "name", "") if fn is not None else ""
            arguments = getattr(fn, "arguments", {}) if fn is not None else {}

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return str(name), arguments

    def answer(
        self,
        *,
        prompt: str,
        context: dict[str, Any],
        runtime_ctx: dict[str, Any],
        allow_actions: bool,
        use_web: bool,
    ) -> CopilotResponse:
        redacted_prompt, redactions = redact_pii(prompt)
        sources = self.index.retrieve(redacted_prompt, k=self.rag_k)
        messages = self._messages_with_retrieval(redacted_prompt, context=context, sources=sources)

        actions: list[dict[str, Any]] = []

        if allow_actions:
            tool_allowlist = self.tools.parse_tool_list(runtime_ctx.get("tool_allowlist"))
            approved_actions = self.tools.parse_tool_list(runtime_ctx.get("approved_actions"))

            if not tool_allowlist:
                tool_allowlist = set(tool.name for tool in self.tools.list_tools())

            tool_specs = self.tools.get_ollama_tool_specs(allowlist=tool_allowlist)
            first = self.client.chat(model=self.model, messages=messages, tools=tool_specs)
            tool_calls = self._extract_tool_calls(first)

            if tool_calls:
                messages.append({"role": "assistant", "content": self._extract_content(first)})
                executed_any = False
                for call in tool_calls:
                    name, args = self._tool_call_parts(call)
                    tool = self.tools.get_tool(name)

                    if tool is None:
                        actions.append({
                            "tool": name,
                            "args": args,
                            "status": "blocked",
                            "reason": "unknown_tool",
                        })
                        continue

                    if name not in tool_allowlist:
                        actions.append({
                            "tool": name,
                            "args": args,
                            "status": "blocked",
                            "reason": "not_allowlisted",
                        })
                        continue

                    if tool.requires_approval and name not in approved_actions:
                        actions.append({
                            "tool": name,
                            "args": args,
                            "status": "pending_approval",
                            "reason": "explicit_approval_required",
                        })
                        continue

                    result = self.tools.execute(name, args, runtime_ctx)
                    executed_any = True
                    actions.append({
                        "tool": name,
                        "args": args,
                        "status": "executed",
                        "mutates_state": bool(tool.mutates_state),
                    })
                    messages.append(
                        {
                            "role": "tool",
                            "name": name,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )

                if executed_any:
                    final_resp = self.client.chat(model=self.model, messages=messages)
                    answer = self._extract_content(final_resp)
                else:
                    answer = self._extract_content(first).strip()
                    pending_names = [a["tool"] for a in actions if a.get("status") == "pending_approval"]
                    if pending_names:
                        approval_note = (
                            "Pending approval before execution: "
                            + ", ".join(sorted(set(pending_names)))
                            + ". Resend with approved_actions including these tool names."
                        )
                        answer = f"{answer}\n\n{approval_note}" if answer else approval_note
            else:
                answer = self._extract_content(first)
        else:
            resp = self.client.chat(model=self.model, messages=messages)
            answer = self._extract_content(resp)

        if use_web and os.getenv("COPILOT_ALLOW_WEB_TOOLS", "False") == "True":
            try:
                import requests

                web_hint = requests.get(
                    "https://duckduckgo.com/?q=nonprofit+best+practices&ia=web",
                    timeout=5,
                ).status_code
                answer += f"\n\n[Web tool check enabled: DuckDuckGo reachable, status={web_hint}]"
            except Exception:
                answer += "\n\n[Web tool check enabled, but external search is currently unavailable.]"

        return CopilotResponse(
            answer=answer.strip(),
            sources=sources,
            actions=actions,
            redactions=redactions,
        )
