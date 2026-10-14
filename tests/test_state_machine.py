from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from ngo_homesuite.domain.store import DomainError
from tests.conftest import build_system


class GrantWorkflow(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.directory = TemporaryDirectory()
        self.system = build_system(Path(self.directory.name))
        self.project = self.system.services.create_project(self.system.admin, "State machine", "100")
        self.grants = {}
        self.events = 1

    @rule(cents=st.integers(min_value=1, max_value=12000))
    def create(self, cents):
        grant = self.system.services.create_grant(
            self.system.fundraiser, self.project, "Generated grant", Decimal(cents) / 100,
        )
        self.grants[grant] = ("draft", cents)
        self.events += 1

    @rule(index=st.integers(min_value=0, max_value=30),
          action=st.sampled_from(["approve", "pay", "cancel"]),
          payer=st.booleans())
    def transition(self, index, action, payer):
        if not self.grants:
            return
        grant = list(self.grants)[index % len(self.grants)]
        status, cents = self.grants[grant]
        reserved = sum(c for state, c in self.grants.values() if state in {"approved", "paid"})
        legal = (
            (action == "approve" and status == "draft" and reserved + cents <= 10000)
            or (action == "pay" and status == "approved" and payer)
            or (action == "cancel" and status in {"draft", "approved"})
        )
        # Approvals always use the first admin; payment randomly uses the same or different admin.
        actor = self.system.payer if action == "pay" and payer else self.system.admin
        try:
            self.system.services.transition_grant(actor, grant, action)
        except DomainError:
            assert not legal
        else:
            assert legal
            self.grants[grant] = ({"approve": "approved", "pay": "paid", "cancel": "cancelled"}[action], cents)
            self.events += 1

    @invariant()
    def budgets_states_and_audits_match(self):
        with self.system.store.transaction("alpha") as conn:
            spent = conn.execute("SELECT spent FROM projects WHERE id=?", (self.project,)).fetchone()[0]
            assert round(spent * 100) == sum(c for state, c in self.grants.values() if state == "paid")
            assert sum(c for state, c in self.grants.values() if state in {"approved", "paid"}) <= 10000
            assert {r["id"]: (r["status"], r["amount_minor"]) for r in conn.execute("SELECT * FROM grants")} == self.grants
            count = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE entity_type IN ('grant', 'project')",
            ).fetchone()[0]
            assert count == self.events
            assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == conn.execute(
                "SELECT COUNT(*) FROM audit_log",
            ).fetchone()[0]

    def teardown(self):
        self.directory.cleanup()


TestGrantWorkflow = GrantWorkflow.TestCase
TestGrantWorkflow.settings = settings(max_examples=15, stateful_step_count=30, deadline=None)
