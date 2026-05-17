from __future__ import annotations

from ngo_homesuite.models.core import db
from ngo_homesuite.utils import email_worker


def test_process_email_queue_sends_pending(shared_test_app, monkeypatch):
    with shared_test_app.app_context():
        email_worker.ensure_email_queue_table()
        email_worker.enqueue_email(to_email="a@example.org", subject="Subj", body="Body")

        monkeypatch.setattr("ngo_homesuite.utils.email_worker.send_email", lambda **_: True)
        result = email_worker.process_email_queue(limit=10)

        assert result["processed"] == 1
        assert result["sent"] == 1

        row = db.session.execute(
            db.text("SELECT status FROM email_queue WHERE to_email = 'a@example.org' ORDER BY id DESC LIMIT 1")
        ).mappings().first()
        assert row["status"] == "sent"


def test_process_email_queue_retries_and_fails(shared_test_app, monkeypatch):
    with shared_test_app.app_context():
        email_worker.ensure_email_queue_table()
        email_worker.enqueue_email(to_email="b@example.org", subject="Subj", body="Body")

        monkeypatch.setattr("ngo_homesuite.utils.email_worker.send_email", lambda **_: False)
        email_worker.process_email_queue(limit=10)
        email_worker.process_email_queue(limit=10)
        email_worker.process_email_queue(limit=10)

        row = db.session.execute(
            db.text("SELECT status, attempts FROM email_queue WHERE to_email = 'b@example.org' ORDER BY id DESC LIMIT 1")
        ).mappings().first()
        assert row["status"] == "failed"
        assert int(row["attempts"]) == 3


def test_retry_failed_emails_requeues(shared_test_app):
    with shared_test_app.app_context():
        email_worker.ensure_email_queue_table()
        db.session.execute(
            db.text(
                """
                INSERT INTO email_queue(to_email, subject, body, status, attempts)
                VALUES ('c@example.org', 'Subj', 'Body', 'failed', 2)
                """
            )
        )
        db.session.commit()

        updated = email_worker.retry_failed_emails(limit=10)
        assert updated == 1
        row = db.session.execute(
            db.text("SELECT status FROM email_queue WHERE to_email = 'c@example.org' ORDER BY id DESC LIMIT 1")
        ).mappings().first()
        assert row["status"] == "pending"
