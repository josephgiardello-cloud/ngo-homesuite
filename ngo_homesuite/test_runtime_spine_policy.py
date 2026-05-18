from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRITICAL_FILES = [
    "ngo_homesuite/main.py",
    "ngo_homesuite/wsgi.py",
    "ngo_homesuite/app/bootstrap.py",
    "ngo_homesuite/app/api.py",
    "ngo_homesuite/db/migrate.py",
    "cli/ngohs_cli/migrate.py",
    "ngo_homesuite/services/campaign_email_service.py",
]

SILENT_EXCEPTION_PATTERNS = (
    "except Exception: pass",
    "except: pass",
    "except BaseException: pass",
)


def test_runtime_spine_delegates_through_main_create_app() -> None:
    main_text = (ROOT / "ngo_homesuite/main.py").read_text(encoding="utf-8")
    wsgi_text = (ROOT / "ngo_homesuite/wsgi.py").read_text(encoding="utf-8")
    bootstrap_text = (ROOT / "ngo_homesuite/app/bootstrap.py").read_text(encoding="utf-8")
    api_text = (ROOT / "ngo_homesuite/app/api.py").read_text(encoding="utf-8")
    cli_text = (ROOT / "cli/ngohs_cli/migrate.py").read_text(encoding="utf-8")

    assert "def create_app(*, compat_mode: bool = False):" in main_text
    assert "raise RuntimeError(\"Non-standard entrypoint blocked\")" in main_text
    assert "from ngo_homesuite.main import create_app" in wsgi_text
    assert "app = create_app(compat_mode=True)" in wsgi_text
    assert "from ngo_homesuite.main import create_app" in bootstrap_text
    assert "app = create_app(compat_mode=True)" in bootstrap_text
    assert "from ngo_homesuite.main import create_app" in api_text
    assert "from ngo_homesuite.db.migrate import auto_migrate" in cli_text
    assert "executescript" not in cli_text


def test_runtime_critical_files_do_not_use_silent_exception_pass() -> None:
    for relative_path in CRITICAL_FILES:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert not any(pattern in text for pattern in SILENT_EXCEPTION_PATTERNS), relative_path
