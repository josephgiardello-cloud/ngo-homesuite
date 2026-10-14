from __future__ import annotations

from typing import Any
import sqlite3

from ..auth.session import require_role
from ..db.connection import run_db
from ..db.utils import print_table
from ..domain.cli import cli_actor, cli_services
from ..domain.store import DomainError
from ..prompts import (
    prompt_int,
    prompt_non_empty,
    prompt_non_negative_amount,
    prompt_optional,
    prompt_positive_amount,
)


@require_role("admin")
def add_project() -> None:
    name = prompt_non_empty("Project name: ")
    description = prompt_optional("Description (optional): ")
    budget = prompt_positive_amount("Budget: ")

    try:
        cli_services().create_project(cli_actor(), name, budget, description)
    except (DomainError, sqlite3.Error) as exc:
        print(f"Could not add project: {exc}")
        return
    print("Project added!")


@require_role("admin")
def update_project_spent() -> None:
    project_id = prompt_int("Project ID: ")

    def get_op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT id, name, budget, spent, start_date FROM projects WHERE id = ?",
            (project_id,),
        )
        return cur.fetchone()

    project = run_db(get_op)
    if not project:
        print("Project not found.")
        return

    _, name, budget, spent, start_date = project
    print("\nCurrent project:")
    try:
        budget_str = f"${float(budget or 0):.2f}"
    except (TypeError, ValueError):
        budget_str = str(budget)
    try:
        spent_str = f"${float(spent or 0):.2f}"
    except (TypeError, ValueError):
        spent_str = str(spent)

    print_table(
        ["ID", "Name", "Budget", "Spent", "Start Date"],
        [(project_id, name, budget_str, spent_str, start_date)],
    )

    new_spent = prompt_non_negative_amount("New spent amount: ")

    try:
        cli_services().set_project_spent(cli_actor(), project_id, new_spent)
    except (DomainError, sqlite3.Error) as exc:
        print(f"Could not update project spent: {exc}")
        return
    print("Project spent updated.")


@require_role("admin")
def view_projects() -> None:
    def op(_conn: Any, cur: Any):
        cur.execute(
            "SELECT id, name, description, budget, spent, start_date FROM projects ORDER BY id DESC"
        )
        return cur.fetchall()

    rows = run_db(op)
    if not rows:
        print("No projects yet.")
        return

    formatted_rows: list[tuple[Any, ...]] = []
    for project_id, name, description, budget, spent, start_date in rows:
        try:
            budget_str = f"${float(budget or 0):.2f}"
        except (TypeError, ValueError):
            budget_str = str(budget)
        try:
            spent_str = f"${float(spent or 0):.2f}"
        except (TypeError, ValueError):
            spent_str = str(spent)
        formatted_rows.append((project_id, name, description, budget_str, spent_str, start_date))

    print_table(["ID", "Name", "Description", "Budget", "Spent", "Start Date"], formatted_rows)
