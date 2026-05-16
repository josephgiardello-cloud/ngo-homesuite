from __future__ import annotations

import importlib
from pathlib import Path


def _load_module():
    return importlib.import_module("ngo_homesuite.db.cron_safe_rotate_db_key")


def test_cron_safe_rotate_db_key_returns_zero_on_success(monkeypatch, tmp_path: Path):
    module = _load_module()
    monkeypatch.setattr(module, "LOCKFILE", str(tmp_path / "rotate.lock"))
    monkeypatch.setattr(module, "LOGFILE", str(tmp_path / "rotate.log"))

    captured: dict[str, str | None] = {}

    def fake_rotate_db_key(*, old_key, new_key):
        captured["old_key"] = old_key
        captured["new_key"] = new_key

    monkeypatch.setattr(module, "rotate_db_key", fake_rotate_db_key)
    monkeypatch.setenv("NGO_HOMESUITE_OLD_KEY", "hex:old")
    monkeypatch.setenv("NGO_HOMESUITE_NEW_KEY", "hex:new")

    assert module.main() == 0
    assert captured == {"old_key": "hex:old", "new_key": "hex:new"}


def test_cron_safe_rotate_db_key_returns_one_on_lock_contention(monkeypatch, tmp_path: Path):
    module = _load_module()
    monkeypatch.setattr(module, "LOCKFILE", str(tmp_path / "rotate.lock"))
    monkeypatch.setattr(module, "LOGFILE", str(tmp_path / "rotate.log"))
    monkeypatch.setattr(module, "_lock_handle", lambda _lock: (_ for _ in ()).throw(BlockingIOError()))

    assert module.main() == 1


def test_cron_safe_rotate_db_key_returns_two_when_rotation_fails(monkeypatch, tmp_path: Path):
    module = _load_module()
    monkeypatch.setattr(module, "LOCKFILE", str(tmp_path / "rotate.lock"))
    monkeypatch.setattr(module, "LOGFILE", str(tmp_path / "rotate.log"))

    def fake_rotate_db_key(*, old_key, new_key):
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "rotate_db_key", fake_rotate_db_key)

    assert module.main() == 2