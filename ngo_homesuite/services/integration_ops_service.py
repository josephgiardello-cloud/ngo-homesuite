from __future__ import annotations

import json
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC
from uuid import uuid4
from typing import Any, Callable

from ngo_homesuite.db.connection import run_db
from ngo_homesuite.db.utils import audit


EventStore = deque[dict[str, Any]]



def _event_store(app, *, maxlen: int = 500) -> EventStore:
    store = app.extensions.get("integration_event_store")
    if store is None:
        store = deque(maxlen=maxlen)
        app.extensions["integration_event_store"] = store
    return store



def record_integration_event(app, *, kind: str, status: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    event = {
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "kind": kind,
        "status": status,
        "details": details or {},
    }
    _event_store(app).append(event)
    audit(action=f"integration.{kind}.{status}", entity_type="integration_event", details=event)
    return event



def summarize_integration_events(app, *, recent_limit: int = 20) -> dict[str, Any]:
    in_memory = list(_event_store(app))

    def _load() -> list[dict[str, Any]]:
        query = (
            "SELECT at_utc, action, details_json FROM audit_log "
            "WHERE action LIKE 'integration.%' ORDER BY id DESC LIMIT ?"
        )

        def _op(_conn: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(query, (max(1, int(recent_limit)) * 5,))
            rows = cur.fetchall() or []
            out: list[dict[str, Any]] = []
            for at_utc, action, details_json in rows:
                details: dict[str, Any] = {}
                if isinstance(details_json, str) and details_json:
                    try:
                        loaded = json.loads(details_json)
                        if isinstance(loaded, dict):
                            details = loaded
                    except ValueError:
                        details = {}

                action_text = str(action or "")
                parts = action_text.split(".")
                kind = parts[1] if len(parts) > 2 else "unknown"
                status = parts[-1] if len(parts) > 1 else "unknown"
                out.append(
                    {
                        "ts": str(at_utc or details.get("ts") or ""),
                        "kind": kind,
                        "status": status,
                        "details": details,
                    }
                )
            return out

        try:
            return run_db(_op) or []
        except Exception:
            return []

    durable = _load()
    events = durable if durable else in_memory
    by_kind = Counter(e["kind"] for e in events)
    by_status = Counter(e["status"] for e in events)
    return {
        "total_events": len(events),
        "by_kind": dict(by_kind),
        "by_status": dict(by_status),
        "recent": events[-max(1, int(recent_limit)):],
    }


def _job_registry(app) -> dict[str, dict[str, Any]]:
    registry = app.extensions.get("integration_job_registry")
    if registry is None:
        registry = {}
        app.extensions["integration_job_registry"] = registry
    return registry


def _executor(app) -> ThreadPoolExecutor:
    executor = app.extensions.get("integration_job_executor")
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="integration-job")
        app.extensions["integration_job_executor"] = executor
    return executor


def submit_background_job(app, *, kind: str, operation: Callable[[], Any]) -> dict[str, Any]:
    job_id = f"ijob_{uuid4().hex}"
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    registry = _job_registry(app)
    registry[job_id] = {
        "job_id": job_id,
        "kind": kind,
        "status": "queued",
        "created_at": created_at,
    }

    app_obj = app._get_current_object()

    def _run() -> None:
        registry[job_id]["status"] = "running"
        try:
            with app_obj.app_context():
                result = operation()
            registry[job_id]["status"] = "completed"
            registry[job_id]["result"] = result
            registry[job_id]["finished_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            record_integration_event(app_obj, kind=kind, status="async_completed", details={"job_id": job_id, "result": result})
        except Exception as exc:
            registry[job_id]["status"] = "failed"
            registry[job_id]["error"] = str(exc)
            registry[job_id]["finished_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            record_integration_event(app_obj, kind=kind, status="async_failed", details={"job_id": job_id, "error": str(exc)})

    if app.config.get("TESTING", False):
        _run()
    else:
        _executor(app_obj).submit(_run)

    return dict(registry[job_id])


def get_background_job(app, job_id: str) -> dict[str, Any] | None:
    return _job_registry(app).get(job_id)


def list_background_jobs(app, *, limit: int = 20) -> list[dict[str, Any]]:
    items = list(_job_registry(app).values())
    items.sort(key=lambda item: str(item.get("created_at") or ""))
    return items[-max(1, int(limit)):]



def run_with_backoff(
    operation: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 0.05,
    factor: float = 2.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Any:
    attempts = max(1, int(attempts))
    delay = max(0.0, float(base_delay_seconds))

    last_exc: Exception | None = None
    for attempt_index in range(attempts):
        try:
            return operation()
        except Exception as exc:  # pragma: no cover - failure branch exercised via route tests when mocked
            last_exc = exc
            if attempt_index >= attempts - 1:
                break
            if delay > 0:
                sleep_fn(delay)
            delay *= max(1.0, float(factor))

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("run_with_backoff failed without exception")
