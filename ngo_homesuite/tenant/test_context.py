from __future__ import annotations

import pytest

from ngo_homesuite.tenant import assert_tenant_match


def test_assert_tenant_match_allows_same_org() -> None:
    assert_tenant_match("org-1", "org-1")


def test_assert_tenant_match_blocks_cross_tenant_access() -> None:
    with pytest.raises(PermissionError, match="Tenant isolation violation"):
        assert_tenant_match("org-1", "org-2")
