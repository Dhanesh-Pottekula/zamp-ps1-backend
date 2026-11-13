"""
SQLite access through SQLAlchemy; owns the declarative Base every table inherits.

SQLAlchemy 2.1's asyncio extension needs greenlet, which is not a pinned dependency, so each unit of work runs
in a worker thread on a plain Session instead of an async engine.
"""

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, override

from sqlalchemy import String, TypeDecorator, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from sqlite3 import Connection

    from sqlalchemy.engine import Dialect


class Base(DeclarativeBase):
    """Declarative base shared by every ORM table."""


class DecimalText(TypeDecorator[Decimal]):
    """Store a Decimal as its exact text so SQLite never rounds money through a float."""

    impl = String(40)
    cache_ok = True

    @override
    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        """Write the Decimal's exact text."""
        return None if value is None else str(value)

    @override
    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        """Read the text back as a Decimal."""
        return None if value is None else Decimal(value)


class _Helper:
    """Connection setup applied to every new SQLite connection."""

    @staticmethod
    def tune_connection(connection: Connection, _record: object) -> None:
        """Enable WAL so API reads never wait behind the pipeline's write transaction."""
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


class DBAdapterClass:
    """Own the engine and run units of work off the event loop."""

    def __init__(self, db_path: Path) -> None:
        """Create the engine for the SQLite file, creating its folder when missing."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        event.listen(self._engine, "connect", _Helper.tune_connection)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    async def run[R](self, work: Callable[[Session], R]) -> R:
        """Run work(session) in one transaction on a worker thread; commit on return, roll back on error."""

        def execute() -> R:
            with self._sessions.begin() as session:
                return work(session)

        return await asyncio.to_thread(execute)

    async def create_tables(self) -> None:
        """Create every table registered on Base that does not exist yet."""
        await asyncio.to_thread(Base.metadata.create_all, self._engine)

    async def close(self) -> None:
        """Dispose of the engine's pooled connections."""
        await asyncio.to_thread(self._engine.dispose)
