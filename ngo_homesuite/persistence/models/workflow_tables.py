from __future__ import annotations

from datetime import datetime

from ngo_homesuite.models.core import db


class WorkflowDefinitionRecord(db.Model):
    __tablename__ = "workflow_definitions_v2"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    workflow_type = db.Column(db.String(120), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    definition_hash = db.Column(db.String(64), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    initial_step = db.Column(db.String(120), nullable=False)
    steps_json = db.Column(db.Text, nullable=False)
    transitions_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("workflow_type", "version", name="uq_workflow_type_version"),
        db.CheckConstraint("LENGTH(workflow_type) > 0", name="ck_workflow_def_type_not_empty"),
    )


class WorkflowInstanceRecord(db.Model):
    __tablename__ = "workflow_instances_v2"

    instance_id = db.Column(db.String(64), primary_key=True)
    org_id = db.Column(db.String(64), nullable=False, index=True)
    workflow_type = db.Column(db.String(120), nullable=False, index=True)
    current_step = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(32), nullable=False)
    history_json = db.Column(db.Text, nullable=False, default="[]")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint("LENGTH(org_id) > 0", name="ck_workflow_instance_org_not_empty"),
        db.Index("ix_workflow_instances_org_workflow", "org_id", "workflow_type"),
    )


class WorkflowEventRecord(db.Model):
    __tablename__ = "workflow_events_v2"

    event_id = db.Column(db.String(64), primary_key=True)
    org_id = db.Column(db.String(64), nullable=False, index=True)
    event_type = db.Column(db.String(120), nullable=False, index=True)
    aggregate_type = db.Column(db.String(120), nullable=False)
    aggregate_id = db.Column(db.String(64), nullable=False, index=True)
    actor_id = db.Column(db.String(64), nullable=False)
    payload_json = db.Column(db.Text, nullable=False, default="{}")
    occurred_at = db.Column(db.String(64), nullable=False, index=True)

    __table_args__ = (
        db.CheckConstraint("LENGTH(org_id) > 0", name="ck_workflow_event_org_not_empty"),
        db.Index("ix_workflow_events_org_occurred", "org_id", "occurred_at"),
        db.Index("ix_workflow_events_org_aggregate", "org_id", "aggregate_id"),
    )
