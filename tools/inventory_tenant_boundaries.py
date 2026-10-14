"""Offline, repeatable tenant-boundary evidence inventory.

No endpoint/tool/job is executed. Runtime introspection uses a separate in-memory
testing app with schedulers/seeding disabled and outbound connections denied.
Static candidates are deliberately over-inclusive and never certified safe.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import inspect
import json
import os
import re
import socket
import sqlite3
import sys
from collections import Counter, deque
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "build", "dist",
}
TENANT_TOKENS = re.compile(r"\b(?:organization_id|org_id|tenant_id|_active_org_id|tenant_context)\b")
EXPORT_TOKENS = re.compile(
    r"export|download|send_file|send_from_directory|csv|xlsx|pdf|backup|excel|spreadsheet|"
    r"Content-Disposition|attachment|octet-stream|archive|zipfile|BytesIO", re.IGNORECASE,
)
WORKER_TOKENS = re.compile(r"worker|scheduler|cron|background|enqueue|dispatch|^process_|^run_|^retry_", re.IGNORECASE)
SQL_EXECUTIONS = {
    "execute", "executemany", "executescript", "exec_driver_sql",
    "query_one", "query_all", "query_scalar", "execute_sql",
    "read_sql", "read_sql_query", "read_sql_table", "run_db",
}
DML_CALLS = {"update", "delete", "insert", "bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings"}
CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?(\w+)", re.IGNORECASE)
TENANT_COLUMN_DDL = re.compile(r"\b(organization_id|org_id)\s+(?:INTEGER|INT|TEXT|VARCHAR)\b", re.IGNORECASE)
REFERENCE_DDL = re.compile(r"\bREFERENCES\s+[\"`\[]?(\w+)", re.IGNORECASE)
SQL_LITERAL = re.compile(
    r"^\s*(?:SELECT\b.+?\bFROM\b|INSERT\s+(?:OR\s+\w+\s+)?INTO\b|"
    r"UPDATE\s+\w+\s+SET\b|DELETE\s+FROM\b|CREATE\s+(?:TABLE|INDEX|TRIGGER|VIEW)\b|"
    r"ALTER\s+TABLE\b|DROP\s+(?:TABLE|INDEX|TRIGGER|VIEW)\b|PRAGMA\b|WITH\b)",
    re.IGNORECASE | re.DOTALL,
)


def source_files(root: Path):
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDED_DIRS and not name.startswith("."))
        for name in sorted(files):
            if name.endswith((".py", ".sql")) and not name.startswith("test_") and name != "conftest.py":
                path = Path(directory) / name
                if not any(part in {"tests", "test", "fixtures"} for part in path.relative_to(root).parts):
                    yield path


def evidence(source: str) -> dict:
    return {
        "tenant_tokens": sorted(set(TENANT_TOKENS.findall(source))),
        "local_tenant_reference": bool(TENANT_TOKENS.search(source)),
        "classification": "unverified_static_evidence_only",
    }


class SourceInventory(ast.NodeVisitor):
    def __init__(self, path: str, source: str):
        self.path, self.source = path, source
        self.stack = []
        self.functions = []
        self.routes = []
        self.exports = []
        self.workers = []
        self.sql = []
        self.sql_literals = []
        self.bypasses = []
        self.tables = []
        self.model_declarations = []
        self.tool_declarations = []

    def location(self, node):
        return {"path": self.path, "line": node.lineno, "owner": ".".join(self.stack)}

    def visit_ClassDef(self, node):
        for statement in node.body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__tablename__" for target in statement.targets
            ):
                self.model_declarations.append({
                    **self.location(node), "class": node.name,
                    "table": ast.unparse(statement.value),
                    "bases": [ast.unparse(base) for base in node.bases],
                    "classification": "declaration_runtime_registration_checked_separately",
                })
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        source = ast.get_source_segment(self.source, node) or ""
        record = {
            **self.location(node), "name": node.name,
            "decorators": [ast.unparse(dec) for dec in node.decorator_list],
            **evidence(source),
        }
        self.functions.append(record)
        route_decorators = [
            dec for dec in node.decorator_list
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in {"route", "get", "post", "put", "patch", "delete"}
        ]
        for dec in route_decorators:
            self.routes.append({
                **record, "route_declaration": ast.unparse(dec),
                "scope": "all_declared_routes_including_conditional_or_unregistered",
            })
        if EXPORT_TOKENS.search(node.name) or EXPORT_TOKENS.search(source):
            self.exports.append({**record, "scope": "export_candidate_not_proven_download"})
        if WORKER_TOKENS.search(node.name) or WORKER_TOKENS.search(self.path):
            self.workers.append({**record, "scope": "worker_or_job_candidate_not_proven_scheduled"})
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        expression = ast.unparse(node)
        if name in SQL_EXECUTIONS | DML_CALLS | {"text"}:
            kind = (
                "sql_text_constructor" if name == "text" else
                "dml_or_bulk_candidate" if name in DML_CALLS else "execution_call"
            )
            self.sql.append({
                **self.location(node), "kind": kind, "callee": ast.unparse(node.func),
                "expression": expression, **evidence(expression),
                "orm_listener_coverage": "not_assumed; raw/Core SQL and bulk DML can bypass",
            })
        if any(keyword.arg == "skip_tenant_rls" for keyword in node.keywords):
            self.bypasses.append({**self.location(node), "expression": expression, **evidence(expression)})
        if name in {"add_job", "Thread", "submit", "delay", "apply_async", "create_task"}:
            self.workers.append({
                **self.location(node), "name": name, "expression": expression,
                "scope": "scheduling_or_thread_boundary", **evidence(expression),
            })
        if name == "MinionTool":
            self.tool_declarations.append({
                **self.location(node), "expression": expression,
                "scope": "static_tool_declaration_including_unregistered", **evidence(expression),
            })
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            if SQL_LITERAL.search(node.value):
                self.sql_literals.append({
                    **self.location(node), "statement": node.value,
                    **evidence(node.value), "scope": "literal_sql_not_proven_execution",
                })
            matches = list(CREATE_TABLE.finditer(node.value))
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(node.value)
                ddl = node.value[match.start():end]
                self.tables.append({
                    **self.location(node), "table": match.group(1),
                    "declared_tenant_tokens": sorted(set(TENANT_TOKENS.findall(ddl))),
                    "declared_tenant_columns": sorted(set(TENANT_COLUMN_DDL.findall(ddl))),
                    "foreign_key_targets": sorted(set(REFERENCE_DDL.findall(ddl))),
                    "classification": "raw_ddl_declaration; constraints_and_runtime_use_unverified",
                })
            if "skip_tenant_rls" in node.value:
                self.bypasses.append({
                    **self.location(node), "expression": node.value,
                    "classification": "bypass_literal_or_listener_check",
                })


def schema_statements(source: str):
    """Split SQLite migration scripts without executing them, including triggers."""
    start = 0
    for index, character in enumerate(source):
        if character == ";" and sqlite3.complete_statement(source[start:index + 1]):
            statement = source[start:index + 1].strip()
            if statement:
                yield source[:start].count("\n") + 1, statement
            start = index + 1
    if source[start:].strip():
        yield source[:start].count("\n") + 1, source[start:].strip()


def legacy_table_inventory(result):
    declarations = {}
    for row in result["raw_table_declarations"]:
        declarations.setdefault(row["table"], []).append(row)
    metadata = {row["table"]: row for row in result["metadata_tables"]}
    edges, roots = {}, {"organizations"}
    for table, rows in declarations.items():
        columns = {key for row in rows for key in row["declared_tenant_columns"]}
        columns.update(metadata.get(table, {}).get("tenant_columns", []))
        if columns:
            roots.add(table)
        edges[table] = {target for row in rows for target in row["foreign_key_targets"]}
    for table, row in metadata.items():
        edges.setdefault(table, set()).update(fk.split(".")[0] for fk in row["foreign_keys"])
        if row["tenant_columns"]:
            roots.add(table)
    inventory = []
    for table, rows in sorted(declarations.items()):
        columns = sorted({key for row in rows for key in row["declared_tenant_columns"]})
        path = None
        queue, visited = deque([[table]]), {table}
        while queue:
            candidate = queue.popleft()
            if candidate[-1] in roots:
                path = candidate
                break
            for target in sorted(edges.get(candidate[-1], set())):
                if target not in visited:
                    queue.append([*candidate, target])
                    visited.add(target)
        classification = (
            "tenant_root" if table == "organizations" else
            "direct_tenant_column_declared" if columns else
            "direct_tenant_column_in_metadata_only" if metadata.get(table, {}).get("tenant_columns") else
            "indirect_tenant_fk_candidate" if path else "global_or_ownership_unknown"
        )
        inventory.append({
            "table": table, "classification": classification,
            "declared_tenant_columns": columns,
            "metadata_tenant_columns": metadata.get(table, {}).get("tenant_columns", []),
            "fk_ownership_candidate_path": path,
            "declarations": [{"path": row["path"], "line": row["line"]} for row in rows],
            "raw_sql_isolation": "unverified; ORM SELECT listener does not protect this table's raw SQL",
            "deployed_schema_verified": False,
        })
    return inventory


def table_paths(metadata):
    """Shortest FK ownership candidate paths; an FK is evidence, not authorization."""
    edges = {
        name: sorted({fk.column.table.name for fk in table.foreign_keys})
        for name, table in metadata.tables.items()
    }
    roots = {
        name for name, table in metadata.tables.items()
        if any(key in table.c for key in ("organization_id", "org_id")) or name == "organizations"
    }
    paths = {}
    for name in edges:
        queue = deque([[name]])
        visited = {name}
        while queue:
            path = queue.popleft()
            if path[-1] in roots:
                paths[name] = path
                break
            for target in edges.get(path[-1], []):
                if target not in visited:
                    visited.add(target)
                    queue.append([*path, target])
    return paths


def runtime_inventory(app, root: Path):
    from ngo_homesuite.ai.minion_tools import MinionToolRegistry
    from ngo_homesuite.db.models import Base
    from ngo_homesuite.db.rls import (
        INDIRECT_TENANT_OWNERS,
        TENANT_READ_EXCLUSIONS,
        tenant_scoped_models,
    )
    from ngo_homesuite.models.core import db

    covered = {model: key for model, key in tenant_scoped_models()}
    models, tables = [], []
    for registry_name, registry, metadata in (
        ("flask_sqlalchemy", db.Model.registry, db.metadata),
        ("legacy_report_base", Base.registry, Base.metadata),
    ):
        paths = table_paths(metadata)
        for name, table in sorted(metadata.tables.items()):
            direct = [key for key in ("organization_id", "org_id") if key in table.c]
            classification = (
                "tenant_root" if name == "organizations" else
                "direct_tenant_column" if direct else
                "indirect_tenant_candidate" if name in paths else "global_or_ownership_unknown"
            )
            tables.append({
                "registry": registry_name, "table": name, "classification": classification,
                "tenant_columns": direct, "fk_ownership_candidate_path": paths.get(name),
                "foreign_keys": sorted(str(fk.target_fullname) for fk in table.foreign_keys),
            })
        for mapper in sorted(registry.mappers, key=lambda m: m.class_.__name__):
            model = mapper.class_
            source_file = Path(inspect.getfile(model)).resolve()
            key = next((key for key in ("organization_id", "org_id") if key in mapper.column_attrs), None)
            classification = (
                "explicit_auth_read_exclusion" if model in TENANT_READ_EXCLUSIONS else
                "direct_tenant_scoped" if model in covered else
                "indirect_tenant_scoped" if model in INDIRECT_TENANT_OWNERS else
                "direct_tenant_uncovered" if key else
                "tenant_root_unscoped" if mapper.local_table.name == "organizations" else
                "indirect_tenant_candidate_unscoped" if mapper.local_table.name in paths else
                "implicit_event_ownership_unscoped" if model.__name__ == "EventDiscountCode" else
                "multi_tenant_security_audit_unscoped" if model.__name__ == "SecurityAuditEvent" else
                "global_or_ownership_unknown"
            )
            models.append({
                "registry": registry_name, "model": model.__name__, "module": model.__module__,
                "path": source_file.relative_to(root).as_posix(), "line": inspect.getsourcelines(model)[1],
                "table": mapper.local_table.name, "tenant_column": key,
                "inherited_tenant_column": bool(key and key not in mapper.local_table.c),
                "inherits": mapper.inherits.class_.__name__ if mapper.inherits else None,
                "classification": classification,
                "exclusion_reason": TENANT_READ_EXCLUSIONS.get(model),
                "fk_ownership_candidate_path": paths.get(mapper.local_table.name),
                "orm_select_scoped": model in covered or model in INDIRECT_TENANT_OWNERS,
                "raw_sql_or_bulk_dml_scoped": False,
                "other_ownership_evidence": (
                    "event_id has no FK; events is a legacy SQL table; explicit callsite ownership required"
                    if model.__name__ == "EventDiscountCode" else
                    "actor_org_id and resource_org_id may differ; global chain semantics; read authorization must be explicit"
                    if model.__name__ == "SecurityAuditEvent" else None
                ),
            })
    routes = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint)):
        function = inspect.unwrap(app.view_functions[rule.endpoint])
        try:
            file = Path(inspect.getfile(function)).resolve()
            source, line = inspect.getsourcelines(function)
            path = file.relative_to(root).as_posix()
            tokens = evidence("".join(source))
        except (TypeError, ValueError, OSError):
            path, line, tokens = None, None, {"classification": "source_unavailable"}
        routes.append({
            "rule": rule.rule, "endpoint": rule.endpoint,
            "methods": sorted(rule.methods - {"HEAD", "OPTIONS"}),
            "path": path, "line": line, "handler": function.__name__,
            "authentication_decorator_mentioned": (
                any(token in "".join(source) for token in ("login_required", "roles_required", "requires_role", "api_auth"))
                if path else None
            ),
            **tokens, "two_tenant_route_test_coverage": "not_inferred_from_static_inventory",
        })
    tools = [
        {
            "name": tool.name, "handler": tool.handler.__name__,
            "path": Path(inspect.getfile(tool.handler)).resolve().relative_to(root).as_posix(),
            "line": inspect.getsourcelines(tool.handler)[1],
            "mutates_state": tool.mutates_state, "requires_approval": tool.requires_approval,
            **evidence(inspect.getsource(tool.handler)),
        }
        for tool in MinionToolRegistry()._tools.values()
    ]
    return {"models": models, "metadata_tables": tables, "runtime_routes": routes, "ai_tools": tools}


def build_inventory(root: Path, app):
    result = {
        "format_version": 1,
        "scope": {
            "root": ".", "python_excluded_directories": sorted(EXCLUDED_DIRS),
            "tests_excluded": True,
            "runtime": "TestingConfig in-memory SQLite; schedulers/seeding disabled; no requests, jobs or tools invoked",
            "additional_model_imports": ["ngo_homesuite.auth.bootstrap", "ngo_homesuite.db.models"],
            "not_certified": [
                "native database RLS", "production backend isolation", "tenant FK consistency",
                "raw SQL or bulk DML isolation", "indirect-model isolation",
                "complete route/export/AI/worker two-tenant coverage", "existing worker audit-context wiring",
                "dynamic SQL/table names or dynamic registrations not represented by AST/runtime profile",
            ],
        },
        "source_files": [], "parse_errors": [], "model_declarations": [],
        "declared_routes": [], "exports": [], "workers": [], "direct_sql": [],
        "skip_tenant_rls": [], "raw_table_declarations": [], "ai_tool_declarations": [],
        "sql_literals": [], "schema_sql_statements": [],
    }
    mapping = {
        "functions": None, "routes": "declared_routes", "exports": "exports", "workers": "workers",
        "sql": "direct_sql", "bypasses": "skip_tenant_rls", "tables": "raw_table_declarations",
        "sql_literals": "sql_literals",
        "model_declarations": "model_declarations", "tool_declarations": "ai_tool_declarations",
    }
    for file in source_files(root):
        source = file.read_text(encoding="utf-8-sig")
        relative = file.relative_to(root).as_posix()
        result["source_files"].append({"path": relative, "sha256": hashlib.sha256(source.encode()).hexdigest()})
        if file.suffix == ".sql":
            for line, statement in schema_statements(source):
                result["schema_sql_statements"].append({
                    "path": relative, "line": line, "statement": statement,
                    "scope": "static_migration_sql_not_executed", **evidence(statement),
                })
                for match in CREATE_TABLE.finditer(statement):
                    result["raw_table_declarations"].append({
                        "path": relative, "line": line + statement[:match.start()].count("\n"),
                        "table": match.group(1),
                        "declared_tenant_tokens": sorted(set(TENANT_TOKENS.findall(statement))),
                        "declared_tenant_columns": sorted(set(TENANT_COLUMN_DDL.findall(statement))),
                        "foreign_key_targets": sorted(set(REFERENCE_DDL.findall(statement))),
                        "classification": "raw_ddl_declaration; constraints_and_runtime_use_unverified",
                    })
            continue
        try:
            visitor = SourceInventory(relative, source)
            visitor.visit(ast.parse(source, filename=relative))
        except SyntaxError as exc:
            result["parse_errors"].append({"path": relative, "line": exc.lineno, "error": str(exc)})
            continue
        for attribute, target in mapping.items():
            if target:
                result[target].extend(getattr(visitor, attribute))
    result.update(runtime_inventory(app, root))
    result["legacy_declared_tables"] = legacy_table_inventory(result)
    registered = {(row["path"], row["model"]) for row in result["models"]}
    for row in result["model_declarations"]:
        row["present_in_runtime_mapper_inventory"] = (row["path"], row["class"]) in registered
    result["counts"] = {
        key: len(value) for key, value in result.items() if isinstance(value, list)
    }
    result["counts"]["model_classifications"] = dict(Counter(row["classification"] for row in result["models"]))
    result["counts"]["sql_classifications"] = dict(Counter(row["kind"] for row in result["direct_sql"]))
    result["counts"]["legacy_table_classifications"] = dict(Counter(
        row["classification"] for row in result["legacy_declared_tables"]
    ))
    result["counts"]["unique_raw_declared_tables"] = len({row["table"] for row in result["raw_table_declarations"]})
    result["counts"]["python_source_files"] = sum(row["path"].endswith(".py") for row in result["source_files"])
    result["counts"]["schema_sql_files"] = sum(row["path"].endswith(".sql") for row in result["source_files"])
    result["counts"]["direct_organization_id_models"] = sum(row["tenant_column"] == "organization_id" for row in result["models"])
    result["counts"]["direct_org_id_models"] = sum(row["tenant_column"] == "org_id" for row in result["models"])
    result["counts"]["inherited_tenant_columns"] = sum(row["inherited_tenant_column"] for row in result["models"])
    result["counts"]["production_bypass_invocations"] = sum(
        row["path"].startswith("ngo_homesuite") and "skip_tenant_rls=" in row["expression"]
        for row in result["skip_tenant_rls"]
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON artifact path inside this repository")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("--output must be inside the repository")
    os.environ.pop("NGO_HOMESUITE_DB_KEY", None)
    from ngo_homesuite.app_factory import create_app
    from ngo_homesuite.flask_config import TestingConfig
    # This model is lazily imported by bootstrap callsites, not ordinary startup.
    importlib.import_module("ngo_homesuite.auth.bootstrap")

    class InventoryConfig(TestingConfig):
        FLASK_ENV = "testing"
        ENABLE_DEMO_SEED = False
        EVENT_REMINDER_SCHEDULER_ENABLED = False
        CAMPAIGN_EMAIL_SCHEDULER_ENABLED = False
        GRANT_SEARCH_SCHEDULER_ENABLED = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    def deny_network(*args, **kwargs):
        raise RuntimeError("Outbound network calls are forbidden during tenant inventory")

    with patch.object(socket.socket, "connect", deny_network), patch.object(socket, "create_connection", deny_network):
        app = create_app(InventoryConfig)
        with app.app_context():
            result = build_inventory(ROOT, app)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"], sort_keys=True))
    return 1 if result["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
