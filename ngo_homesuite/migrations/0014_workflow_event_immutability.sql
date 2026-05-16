-- Migration 0014: enforce append-only immutability for workflow_events_v2

CREATE TRIGGER IF NOT EXISTS trg_workflow_events_v2_no_update
BEFORE UPDATE ON workflow_events_v2
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'workflow_events_v2 is append-only; UPDATE is not allowed');
END;

CREATE TRIGGER IF NOT EXISTS trg_workflow_events_v2_no_delete
BEFORE DELETE ON workflow_events_v2
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'workflow_events_v2 is append-only; DELETE is not allowed');
END;