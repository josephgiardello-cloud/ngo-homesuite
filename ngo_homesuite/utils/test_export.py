from __future__ import annotations

import sqlite3

import pytest

from ngo_homesuite.utils import export as export_mod


def test_build_export_query_scopes_by_organization_id_for_tenant_table() -> None:
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE donors (id INTEGER PRIMARY KEY, organization_id INTEGER, name TEXT)")

    sql, params = export_mod._build_export_query(
        cur,
        table="donors",
        columns_sql="id, name",
        organization_id=7,
        tenant_mode=True,
    )

    assert "FROM donors" in sql
    assert "WHERE organization_id = ?" in sql
    assert params == (7,)


def test_build_export_query_uses_join_for_allocations_without_direct_org_column() -> None:
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE donation_allocations (id INTEGER PRIMARY KEY, donation_id INTEGER, project_id INTEGER, category TEXT, amount REAL)")
    cur.execute("CREATE TABLE donations (id INTEGER PRIMARY KEY, organization_id INTEGER)")

    sql, params = export_mod._build_export_query(
        cur,
        table="donation_allocations",
        columns_sql="id, donation_id, project_id, category, amount",
        organization_id=3,
        tenant_mode=True,
    )

    assert "JOIN donations" in sql
    assert "donations.organization_id = ?" in sql
    assert params == (3,)


def test_build_export_query_requires_org_scope_when_tenant_mode_enabled() -> None:
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE donors (id INTEGER PRIMARY KEY, organization_id INTEGER, name TEXT)")

    with pytest.raises(ValueError, match="requires organization_id"):
        export_mod._build_export_query(
            cur,
            table="donors",
            columns_sql="id, name",
            organization_id=None,
            tenant_mode=True,
        )
