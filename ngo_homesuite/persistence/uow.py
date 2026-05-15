from __future__ import annotations

from ngo_homesuite.models.core import db


class SqlAlchemyUnitOfWork:
    """Explicit transaction boundary for deterministic write paths."""

    def __init__(self) -> None:
        self._active = False

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self._active = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.rollback()
        elif self._active:
            self.commit()
        self._active = False

    def commit(self) -> None:
        db.session.commit()

    def rollback(self) -> None:
        db.session.rollback()
