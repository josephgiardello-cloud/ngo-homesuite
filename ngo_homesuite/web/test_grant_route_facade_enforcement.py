from __future__ import annotations

from pathlib import Path


def test_web_routes_do_not_import_legacy_grant_services_directly() -> None:
    root = Path(__file__).resolve().parents[2]
    web_dir = root / "ngo_homesuite" / "web"

    disallowed = (
        "from ngo_homesuite.services.grant_service import",
        "from ngo_homesuite.services import grant_service",
        "from ngo_homesuite.services.grant_preaward_service import",
        "from ngo_homesuite.services.grant_outcomes_service import",
        "from ngo_homesuite.services.grant_approval_service import",
        "from ngo_homesuite.services.grant_accounting_policy_service import",
    )

    violations: list[str] = []
    for path in web_dir.rglob("*.py"):
        if path.name.startswith("test_grant_route_facade_enforcement"):
            continue
        text = path.read_text(encoding="utf-8-sig")
        for needle in disallowed:
            if needle in text:
                rel = path.relative_to(root).as_posix()
                violations.append(f"{rel}: contains '{needle}'")

    assert not violations, "Routes must use GrantsFacade only. Violations: " + "; ".join(violations)
