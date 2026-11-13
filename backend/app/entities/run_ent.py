"""
Runs and their steps, plus the in-process event bus the live view follows.

Each subscriber gets its own queue per run. A follower subscribes before reading stored steps, so nothing published
in between is lost; a step already sent in a final state never goes back to running, and a final state is sent once.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, delete, exists, insert, literal, select, update
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters import Base
from app.entities._types import StageTypes

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection

    from sqlalchemy.orm import Session

    from app.adapters import AdapterTypes


class RunDB:
    """Run and step tables."""

    class Runs(Base):
        """One processing attempt of one document."""

        __tablename__ = "runs"

        id: Mapped[str] = mapped_column(String(32), primary_key=True)
        filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
        sample: Mapped[str | None] = mapped_column(ForeignKey("samples.id"), index=True, nullable=True)
        status: Mapped[str] = mapped_column(String(16))
        outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
        vendor_id: Mapped[str | None] = mapped_column(ForeignKey("vendors.vendor_id"), index=True, nullable=True)
        po_number: Mapped[str | None] = mapped_column(ForeignKey("orders.po_number"), nullable=True)
        invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
        summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
        mode: Mapped[str] = mapped_column(String(10))
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
        millis: Mapped[int | None] = mapped_column(Integer, nullable=True)

    class Steps(Base):
        """The final state of one stage of a run."""

        __tablename__ = "steps"

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
        name: Mapped[str] = mapped_column(String(16))
        state: Mapped[str] = mapped_column(String(10))
        millis: Mapped[int] = mapped_column(Integer)
        data: Mapped[dict[str, Any]] = mapped_column(JSON)
        findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON)


class _Types:
    """Run views."""

    class RunView(BaseModel):
        """One run as listed in history."""

        id: str
        filename: str | None
        sample: str | None
        status: StageTypes.RunStatus
        outcome: StageTypes.Outcome | None
        vendor_id: str | None
        po_number: str | None
        invoice_id: int | None
        summary: str | None
        mode: StageTypes.RunMode
        created_at: datetime
        millis: int | None

    class RunDetail(RunView):
        """One run with its stored steps in stage order and the token usage its extraction step recorded."""

        steps: list[StageTypes.StepEvent]
        usage: StageTypes.Usage | None


class RunTypes:
    """Public run types."""

    RunView = _Types.RunView
    type StreamItem = StageTypes.StepEvent | StageTypes.RunClosed


class _Helper:
    """Row conversions."""

    @staticmethod
    def view(row: RunDB.Runs) -> _Types.RunView:
        """Turn a run row into its listed view."""
        return _Types.RunView.model_validate(
            {
                "id": row.id,
                "filename": row.filename,
                "sample": row.sample,
                "status": row.status,
                "outcome": row.outcome,
                "vendor_id": row.vendor_id,
                "po_number": row.po_number,
                "invoice_id": row.invoice_id,
                "summary": row.summary,
                "mode": row.mode,
                "created_at": row.created_at.replace(tzinfo=row.created_at.tzinfo or UTC),
                "millis": row.millis,
            }
        )

    @staticmethod
    def step(row: RunDB.Steps) -> StageTypes.StepEvent:
        """Turn a step row into its event."""
        return StageTypes.StepEvent.model_validate(
            {
                "run_id": row.run_id,
                "name": row.name,
                "state": row.state,
                "millis": row.millis,
                "data": row.data,
                "findings": row.findings,
            }
        )

    @staticmethod
    def step_values(event: StageTypes.StepEvent) -> dict[str, Any]:
        """Return the column values of a final step."""
        return {
            "run_id": event.run_id,
            "name": event.name,
            "state": event.state,
            "millis": event.millis,
            "data": event.data,
            "findings": [finding.model_dump() for finding in event.findings],
        }

    @staticmethod
    def usage(stored: dict[str, int] | None) -> StageTypes.Usage | None:
        """Read the usage an extraction step stored; a skipped, failed or keyless extraction has none."""
        return StageTypes.Usage.model_validate(stored) if stored else None

    @staticmethod
    def steps_in(session: Session, run_id: str) -> list[StageTypes.StepEvent]:
        """Load a run's stored steps in the order they were stored."""
        query = select(RunDB.Steps).where(RunDB.Steps.run_id == run_id).order_by(RunDB.Steps.id)
        return [_Helper.step(row) for row in session.scalars(query)]


class RunEntityClass:
    """Own runs, steps and the live event bus."""

    def __init__(self, db_adapter: AdapterTypes.DB) -> None:
        """Keep the database and start with no subscribers."""
        self._db_adapter = db_adapter
        self._subscribers: dict[str, set[asyncio.Queue[RunTypes.StreamItem]]] = {}

    async def initialize(self) -> None:
        """Close runs a previous process left running; their tasks died with it."""
        query = update(RunDB.Runs).where(RunDB.Runs.status == "running")
        values = {
            "status": "error",
            "summary": "The server stopped while this run was in progress; nothing was approved.",
        }
        await self._db_adapter.run(lambda session: session.execute(query.values(**values)))

    async def start(self, filename: str | None, sample: str | None, mode: StageTypes.RunMode) -> str:
        """Create a running run and return its id; only an upload with a usable name has a filename."""
        run_id = uuid.uuid4().hex
        row = RunDB.Runs(
            id=run_id,
            filename=filename,
            sample=sample,
            status="running",
            mode=mode,
            created_at=datetime.now(UTC),
        )
        await self._db_adapter.run(lambda session: session.add(row))
        return run_id

    def publish(self, event: StageTypes.StepEvent | StageTypes.RunClosed, run_id: str) -> None:
        """Hand an event to every follower of the run."""
        for queue in self._subscribers.get(run_id, set()):
            queue.put_nowait(event)

    async def record(self, event: StageTypes.StepEvent) -> None:
        """
        Store a final step, then publish it; a running step is only published.

        The insert and the check that the run still exists are one statement, so a step of a run a reset deleted
        is never stored, even when the reset lands between two steps.
        """
        if event.state != "running":
            values = _Helper.step_values(event)
            columns = RunDB.Steps.__table__.c
            alive = select(*(literal(value, columns[key].type).label(key) for key, value in values.items())).where(
                exists().where(RunDB.Runs.id == event.run_id)
            )
            statement = insert(RunDB.Steps).from_select(list(values), alive)
            await self._db_adapter.run(lambda session: session.execute(statement))

        self.publish(event, event.run_id)

    def record_in(self, session: Session, event: StageTypes.StepEvent) -> None:
        """Store a final step inside the caller's transaction."""
        session.add(RunDB.Steps(**_Helper.step_values(event)))

    def close_in(self, session: Session, run_id: str, closing: StageTypes.RunClosing) -> bool:
        """
        Write the closing record on the run inside the caller's transaction; return False when a reset deleted it.

        It is a single UPDATE, so when it is a transaction's first statement it also takes SQLite's write lock: a
        reset cannot delete the run between this check and the transaction's later writes.
        """
        result = session.execute(update(RunDB.Runs).where(RunDB.Runs.id == run_id).values(**closing.model_dump()))
        return result.rowcount == 1

    def link_in(self, session: Session, run_id: str, invoice_id: int) -> None:
        """Point a closed run at the register row its persist stage created, inside the caller's transaction."""
        session.execute(update(RunDB.Runs).where(RunDB.Runs.id == run_id).values(invoice_id=invoice_id))

    async def close(self, run_id: str, closing: StageTypes.RunClosing) -> None:
        """Write the closing record in its own transaction and announce it; a deleted run is only announced."""
        await self._db_adapter.run(lambda session: self.close_in(session, run_id, closing))
        self.announce(run_id, closing)

    def announce(self, run_id: str, closing: StageTypes.RunClosing) -> None:
        """Publish the final event of a run."""
        closed = StageTypes.RunClosed(status=closing.status, outcome=closing.outcome, summary=closing.summary)
        self.publish(closed, run_id)

    def clear_in(self, session: Session) -> None:
        """Delete every run and step inside the caller's transaction."""
        session.execute(delete(RunDB.Steps))
        session.execute(delete(RunDB.Runs))

    def end_streams(self, summary: str) -> None:
        """Close every open follower stream, for example after a reset deleted their runs."""
        for run_id in list(self._subscribers):
            self.publish(StageTypes.RunClosed(status="error", outcome=None, summary=summary), run_id)

    async def history(self, outcome: StageTypes.Outcome | None) -> list[_Types.RunView]:
        """Return runs newest first, optionally only those with one outcome."""
        query = select(RunDB.Runs).order_by(RunDB.Runs.created_at.desc())
        query = query if outcome is None else query.where(RunDB.Runs.outcome == outcome)
        return await self._db_adapter.run(lambda session: [_Helper.view(row) for row in session.scalars(query)])

    async def detail(self, run_id: str) -> _Types.RunDetail | None:
        """Return one run with its steps, or None when it does not exist."""

        def load(session: Session) -> _Types.RunDetail | None:
            row = session.get(RunDB.Runs, run_id)
            if row is None:
                return None

            steps = _Helper.steps_in(session, run_id)
            extraction = next((step for step in steps if step.name == "extraction"), None)
            return _Types.RunDetail(
                **_Helper.view(row).model_dump(),
                steps=steps,
                usage=_Helper.usage(extraction.data.get("usage") if extraction else None),
            )

        return await self._db_adapter.run(load)

    async def usages(self, run_ids: Collection[str]) -> dict[str, StageTypes.Usage]:
        """Return the token usage of each run's extraction step, by run id; runs without usage are absent."""
        query = select(RunDB.Steps.run_id, RunDB.Steps.data["usage"]).where(
            RunDB.Steps.name == "extraction", RunDB.Steps.run_id.in_(run_ids)
        )
        rows = await self._db_adapter.run(lambda session: session.execute(query).all())
        return {run_id: usage for run_id, stored in rows if (usage := _Helper.usage(stored)) is not None}

    async def sample_states(self) -> dict[str, _Types.RunView]:
        """Return the latest run of every sample that has run since the last reset."""
        query = select(RunDB.Runs).where(RunDB.Runs.sample.is_not(None)).order_by(RunDB.Runs.created_at)
        rows = await self._db_adapter.run(lambda session: [_Helper.view(row) for row in session.scalars(query)])
        return {row.sample: row for row in rows if row.sample is not None}

    async def follow(self, run_id: str) -> AsyncIterator[RunTypes.StreamItem]:
        """Yield the run's stored steps, then its live events, ending with the closing event."""
        queue: asyncio.Queue[RunTypes.StreamItem] = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(queue)
        try:
            detail = await self.detail(run_id)
            if detail is None:
                return

            finished: set[str] = set()
            for step in detail.steps:
                finished.add(step.name)
                yield step

            if detail.status != "running":
                yield StageTypes.RunClosed(status=detail.status, outcome=detail.outcome, summary=detail.summary)
                return

            while True:
                item = await queue.get()
                if isinstance(item, StageTypes.RunClosed):
                    yield item
                    return

                if item.name in finished:
                    continue

                if item.state != "running":
                    finished.add(item.name)

                yield item
        finally:
            followers = self._subscribers.get(run_id, set())
            followers.discard(queue)
            if not followers:
                self._subscribers.pop(run_id, None)
