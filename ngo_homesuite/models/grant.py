from __future__ import annotations

import sqlite3
from typing import Literal

from ..auth.session import require_role
from ..db.utils import print_table
from ..domain.cli import cli_actor, cli_services
from ..domain.store import DomainError
from ..prompts import prompt_int, prompt_non_empty, prompt_positive_amount


@require_role("admin", "fundraiser")
def add_grant() -> None:
    project_id = prompt_int("Project ID: ")
    name = prompt_non_empty("Grant name: ")
    amount = prompt_positive_amount("Grant amount: ")
    try:
        grant_id = cli_services().create_grant(cli_actor(), project_id, name, amount)
        print(f"Grant draft created: {grant_id}")
    except (DomainError, sqlite3.Error) as exc:
        print(f"Could not create grant: {exc}")


@require_role("admin", "fundraiser", "viewer")
def view_grants() -> None:
    rows = cli_services().list_entities(cli_actor(), "grants")
    print_table(["ID", "Name", "Project", "Cents", "Status"], [
        (row["id"], row["name"], row["project_id"], row["amount_minor"], row["status"]) for row in rows
    ])


@require_role("admin")
def transition_grant() -> None:
    grant_id = prompt_int("Grant ID: ")
    action = prompt_non_empty("Action (approve/pay/cancel): ").lower()
    if action not in {"approve", "pay", "cancel"}:
        print("Invalid action.")
        return
    typed_action: Literal["approve", "pay", "cancel"] = action
    try:
        cli_services().transition_grant(cli_actor(), grant_id, typed_action)
        print("Grant updated.")
    except (DomainError, sqlite3.Error) as exc:
        print(f"Could not update grant: {exc}")
