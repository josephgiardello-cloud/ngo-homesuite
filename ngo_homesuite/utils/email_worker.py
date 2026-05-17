from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from ngo_homesuite.models.core import db
from ngo_homesuite.utils.email import send_email


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_email_queue_table() -> None:
    db.session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS email_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                to_email TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                sent_at TEXT
            )
            """
        )
    )
    db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_email_queue_status ON email_queue(status)"))
    db.session.commit()


def enqueue_email(*, to_email: str, subject: str, body: str) -> int:
    ensure_email_queue_table()
    db.session.execute(
        text(
            """
            INSERT INTO email_queue(to_email, subject, body, status, attempts)
            VALUES (:to_email, :subject, :body, 'pending', 0)
            """
        ),
        {
            "to_email": str(to_email).strip().lower(),
            "subject": str(subject),
            "body": str(body),
        },
    )
    row_id = int(db.session.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"])
    db.session.commit()
    return row_id


def process_email_queue(*, limit: int = 200) -> dict[str, int]:
    ensure_email_queue_table()
    rows = db.session.execute(
        text(
            """
            SELECT id, to_email, subject, body, attempts
            FROM email_queue
            WHERE status = 'pending' AND attempts < 3
            ORDER BY id ASC
            LIMIT :limit
            """
        ),
        {"limit": int(limit)},
    ).mappings().all()

    sent = 0
    failed = 0
    for row in rows:
        queue_id = int(row["id"])
        try:
            ok = send_email(to=row["to_email"], subject=row["subject"], context={"text": row["body"]})
        except Exception as exc:  # pragma: no cover
            ok = False
            error = str(exc)
        else:
            error = "delivery_failed"

        if ok:
            db.session.execute(
                text(
                    """
                    UPDATE email_queue
                    SET status = 'sent', attempts = attempts + 1, sent_at = :sent_at, last_error = NULL
                    WHERE id = :id
                    """
                ),
                {"id": queue_id, "sent_at": _utcnow_iso()},
            )
            sent += 1
            continue

        db.session.execute(
            text(
                """
                UPDATE email_queue
                SET attempts = attempts + 1,
                    status = CASE WHEN attempts + 1 >= 3 THEN 'failed' ELSE 'pending' END,
                    last_error = :error
                WHERE id = :id
                """
            ),
            {"id": queue_id, "error": error},
        )
        failed += 1

    db.session.commit()
    return {"processed": len(rows), "sent": sent, "failed": failed}


def retry_failed_emails(*, limit: int = 200) -> int:
    ensure_email_queue_table()
    result = db.session.execute(
        text(
            """
            UPDATE email_queue
            SET status = 'pending'
            WHERE id IN (
                SELECT id FROM email_queue
                WHERE status = 'failed' AND attempts < 3
                ORDER BY id ASC
                LIMIT :limit
            )
            """
        ),
        {"limit": int(limit)},
    )
    db.session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def list_email_queue(*, limit: int = 100) -> list[dict[str, object]]:
    ensure_email_queue_table()
    rows = db.session.execute(
        text(
            """
            SELECT id, to_email, subject, status, attempts, last_error, created_at, sent_at
            FROM email_queue
            ORDER BY id DESC
            LIMIT :limit
            """
        ),
        {"limit": int(limit)},
    ).mappings().all()
    return [dict(r) for r in rows]
