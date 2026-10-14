from __future__ import annotations

import ast

from ngo_homesuite.ai.minion_tools import MinionToolRegistry
from tools.inventory_tenant_boundaries import (
    ROOT,
    SourceInventory,
    runtime_inventory,
    schema_statements,
)


def test_inventory_records_boundaries_without_certifying_safety():
    source = '''
class Child(db.Model):
    __tablename__ = "child"

@bp.get("/export")
@login_required
def download_csv():
    db.session.execute(text("SELECT * FROM child WHERE organization_id = :org"))
    db.session.query(Child).delete()
    return send_file("private.csv")

async def process_background():
    scheduler.add_job(download_csv)
    statement.execution_options(skip_tenant_rls=True)
    sql = "CREATE TABLE IF NOT EXISTS queued_email (id INTEGER, body TEXT)"
'''
    visitor = SourceInventory("sample.py", source)
    visitor.visit(ast.parse(source))
    assert len(visitor.model_declarations) == 1
    assert len(visitor.routes) == 1
    assert visitor.routes[0]["tenant_tokens"] == ["organization_id"]
    assert {row["name"] for row in visitor.exports} == {"download_csv", "process_background"}
    assert {row["kind"] for row in visitor.sql} == {
        "execution_call", "sql_text_constructor", "dml_or_bulk_candidate",
    }
    assert all(row["classification"] == "unverified_static_evidence_only" for row in visitor.sql)
    assert any(row["scope"] == "scheduling_or_thread_boundary" for row in visitor.workers)
    assert visitor.bypasses[0]["expression"].endswith("skip_tenant_rls=True)")
    assert visitor.tables[0]["table"] == "queued_email"
    assert visitor.tables[0]["declared_tenant_tokens"] == []


def test_runtime_inventory_lists_all_models_routes_and_ai_tools(shared_test_app):
    with shared_test_app.app_context():
        result = runtime_inventory(shared_test_app, ROOT)
    assert len(result["runtime_routes"]) == len(list(shared_test_app.url_map.iter_rules()))
    assert {row["name"] for row in result["ai_tools"]} == set(MinionToolRegistry()._tools)
    models = {row["model"]: row for row in result["models"]}
    assert models["User"]["classification"] == "explicit_auth_read_exclusion"
    assert models["Volunteer"]["orm_select_scoped"]
    assert models["WorkflowInstanceRecord"]["tenant_column"] == "org_id"
    assert models["AIMessage"]["classification"] == "indirect_tenant_scoped"
    assert models["EventDiscountCode"]["classification"] == "implicit_event_ownership_unscoped"
    assert models["SecurityAuditEvent"]["classification"] == "multi_tenant_security_audit_unscoped"
    assert not any(row["raw_sql_or_bulk_dml_scoped"] for row in result["models"])
    assert all(row["classification"] == "unverified_static_evidence_only" for row in result["ai_tools"])


def test_schema_inventory_retains_trigger_as_single_statement():
    source = (
        "CREATE TABLE child (org_id TEXT);\n"
        "CREATE TRIGGER prevent_delete BEFORE DELETE ON child BEGIN "
        "SELECT RAISE(ABORT, 'do not; delete'); END;\n"
        "ALTER TABLE child ADD COLUMN body TEXT;"
    )
    statements = list(schema_statements(source))
    assert len(statements) == 3
    assert "END;" in statements[1][1]
    assert statements[2][1].startswith("ALTER TABLE")
