import sqlite3

import pytest

from backend.app.worker import SQLiteEventSink, main


def test_configured_worker_uses_durable_outbox_and_idempotent_inbox(system, tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "tenants.json"
    manifest.write_text('{"alpha": "alpha.db", "beta": "beta.db"}')
    sink = tmp_path / "inbox.db"
    monkeypatch.setattr("sys.argv", ["worker", str(manifest), "alpha", str(sink)])
    main()
    assert "Delivered 4 events" in capsys.readouterr().out
    main()
    assert "Delivered 0 events" in capsys.readouterr().out
    with sqlite3.connect(sink) as conn:
        rows = conn.execute("SELECT event_key, payload FROM received").fetchall()
        assert len(rows) == 4
        assert all(key.startswith("alpha:") for key, _ in rows)
    receiver = SQLiteEventSink(sink)
    receiver(*rows[0])
    with pytest.raises(ValueError, match="different content"):
        receiver(rows[0][0], "different payload")


@pytest.mark.parametrize("target_tenant", ["alpha", "beta"])
def test_worker_rejects_business_database_as_sink(system, tmp_path, monkeypatch, target_tenant):
    manifest = tmp_path / "tenants.json"
    manifest.write_text('{"alpha": "alpha.db", "beta": "beta.db"}')
    monkeypatch.setattr("sys.argv", ["worker", str(manifest), "alpha", str(system.paths[target_tenant])])
    with pytest.raises(SystemExit):
        main()
