from __future__ import annotations

import logging

from ngo_homesuite.events import scheduler as scheduler_module
from ngo_homesuite.events import services as events_services
from ngo_homesuite.models.core import db
from ngo_homesuite.utils import email_worker


def test_scheduler_guard_captures_exceptions(caplog):
    def _boom():
        raise RuntimeError("synthetic scheduler crash")

    with caplog.at_level(logging.ERROR):
        scheduler_module._run_with_guard("failure-injection-job", _boom)

    assert "scheduler job failed" in caplog.text


def test_event_queue_dead_letter_for_suppressed_email(shared_test_app):
    with shared_test_app.app_context():
        events_services._ensure_email_tables()
        now_iso = events_services._utcnow().isoformat()
        db.session.execute(
            db.text(
                """
                INSERT INTO event_email_queue(
                    event_id, attendee_email, attendee_name, hours_before,
                    scheduled_for, status, attempt_count, max_attempts,
                    next_attempt_at, opt_out_token
                )
                VALUES(1, 'suppressed@example.org', 'Suppressed', 24, :scheduled_for, 'pending', 0, 3, :next_attempt_at, 'tok-sup')
                """
            ),
            {"scheduled_for": now_iso, "next_attempt_at": now_iso},
        )
        db.session.commit()

        events_services.mark_email_bounced("suppressed@example.org", reason="manual_suppression")
        result = events_services.process_event_email_queue(limit=10)

        assert int(result["dead_lettered"]) >= 1
        dl = db.session.execute(
            db.text("SELECT error_code FROM event_email_dead_letter WHERE attendee_email = 'suppressed@example.org' ORDER BY id DESC LIMIT 1")
        ).mappings().first()
        assert dl is not None
        assert dl["error_code"] == "suppressed"


def test_email_dead_letter_is_idempotent_across_reprocessing(shared_test_app, monkeypatch):
    with shared_test_app.app_context():
        email_worker.ensure_email_queue_table()
        email_worker.enqueue_email(to_email="idempotent-deadletter@example.org", subject="Subj", body="Body")

        monkeypatch.setattr("ngo_homesuite.utils.email_worker.send_email", lambda **_: False)
        email_worker.process_email_queue(limit=10)
        email_worker.process_email_queue(limit=10)
        email_worker.process_email_queue(limit=10)
        email_worker.process_email_queue(limit=10)

        row = db.session.execute(
            db.text(
                """
                SELECT COUNT(*) AS count
                FROM email_queue_dead_letter
                WHERE to_email = 'idempotent-deadletter@example.org'
                """
            )
        ).mappings().first()
        assert row is not None
        assert int(row["count"]) == 1