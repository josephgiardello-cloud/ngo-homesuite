from __future__ import annotations

from dataclasses import dataclass

from ngo_homesuite.ai.minion_service import HomeSuiteMinion


@dataclass
class _StubTool:
    name: str
    requires_approval: bool = False
    mutates_state: bool = False


class _StubTools:
    def __init__(self):
        self._tools = {
            "create_donor": _StubTool("create_donor", requires_approval=True, mutates_state=True),
            "search_donors": _StubTool("search_donors", requires_approval=False, mutates_state=False),
        }

    def parse_tool_list(self, raw, *, default_all=True):
        if raw is None:
            return set(self._tools.keys()) if default_all else set()
        if isinstance(raw, str):
            vals = [p.strip() for p in raw.split(",") if p.strip()]
        else:
            vals = [str(p).strip() for p in raw if str(p).strip()]
        return {v for v in vals if v in self._tools}

    def list_tools(self):
        return list(self._tools.values())

    def get_ollama_tool_specs(self, allowlist=None):
        allow = set(allowlist or self._tools.keys())
        return [{"function": {"name": t.name}} for t in self._tools.values() if t.name in allow]

    def get_tool(self, name):
        return self._tools.get(name)

    def execute(self, name, args, runtime_ctx):
        return {"ok": True, "name": name, "args": args}


class _StubIndex:
    def retrieve(self, prompt, k=6):
        return []


class _StubClientPending:
    def __init__(self):
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        return {
            "message": {
                "content": "I can perform that action.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "create_donor",
                            "arguments": {"name": "Jane Donor"},
                        }
                    }
                ],
            }
        }


class _StubClientApproved:
    def __init__(self):
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "message": {
                    "content": "I'll execute the requested action.",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "create_donor",
                                "arguments": {"name": "Jane Donor"},
                            }
                        }
                    ],
                }
            }
        return {
            "message": {
                "content": "Created donor Jane Donor successfully.",
                "tool_calls": [],
            }
        }


def _make_minion(client):
    cp = HomeSuiteMinion.__new__(HomeSuiteMinion)
    cp.client = client
    cp.model = "test-model"
    cp.rag_k = 3
    cp.index = _StubIndex()
    cp.tools = _StubTools()
    return cp


def test_pending_approval_action_is_not_executed_without_explicit_approval():
    cp = _make_minion(_StubClientPending())

    res = cp.answer(
        prompt="Create donor Jane Donor",
        context={"active_page": "donors"},
        runtime_ctx={
            "tool_allowlist": ["create_donor"],
            "approved_actions": [],
            "organization_id": 1,
            "actor": "tester",
        },
        allow_actions=True,
        use_web=False,
    )

    assert any(a.get("status") == "pending_approval" for a in res.actions)
    assert "Pending approval" in res.answer


def test_missing_approved_actions_does_not_grant_implicit_approval():
    cp = _make_minion(_StubClientPending())

    res = cp.answer(
        prompt="Create donor Jane Donor",
        context={"active_page": "donors"},
        runtime_ctx={
            "tool_allowlist": ["create_donor"],
            "organization_id": 1,
            "actor": "tester",
        },
        allow_actions=True,
        use_web=False,
    )

    assert any(a.get("status") == "pending_approval" for a in res.actions)


def test_approved_action_executes_and_returns_executed_status():
    cp = _make_minion(_StubClientApproved())

    res = cp.answer(
        prompt="Create donor Jane Donor",
        context={"active_page": "donors"},
        runtime_ctx={
            "tool_allowlist": ["create_donor"],
            "approved_actions": ["create_donor"],
            "organization_id": 1,
            "actor": "tester",
        },
        allow_actions=True,
        use_web=False,
    )

    assert any(a.get("status") == "executed" and a.get("tool") == "create_donor" for a in res.actions)
    assert "Created donor" in res.answer

