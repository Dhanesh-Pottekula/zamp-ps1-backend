"""
Invoice register: every run that produced an extraction is registered with the keys the duplicate stage compares.

The duplicate check itself is stages/duplicate_stage.py; it reads approved invoices through approved().
"""

from datetime import date  # noqa: TC003 - SQLAlchemy resolves column types at runtime
from decimal import Decimal  # noqa: TC003 - SQLAlchemy and pydantic resolve column and field types at runtime
from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlalchemy import Date, Integer, String, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters import Base, DecimalText
from app.entities._types import IdentityTypes, StageTypes

if TYPE_CHECKING:
    from collections.abc import Collection

    from sqlalchemy.orm import Session

    from app.adapters import AdapterTypes
    from app.entities._types import ExtractionTypes


class InvoiceDB:
    """Invoice register table."""

    class Invoices(Base):
        """One invoice seen by a run, with the keys the duplicate check compares."""

        __tablename__ = "invoices"

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        run_id: Mapped[str] = mapped_column(String(32), index=True)
        vendor_id: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
        po_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
        invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
        number_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
        invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
        taxable: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
        currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
        fingerprint: Mapped[str] = mapped_column(String(2000))
        outcome: Mapped[str] = mapped_column(String(20))


class _Types:
    """Register reads."""

    class BriefFetch(BaseModel):
        """The printed number, taxable value and currency of one registered invoice."""

        invoice_number: str | None
        taxable: Decimal | None
        currency: str | None

    class Registered(BaseModel):
        """An approved invoice of the register with the keys the duplicate stage compares."""

        run_id: str
        invoice_number: str | None
        number_key: str | None
        invoice_date: date | None
        taxable: Decimal | None
        fingerprint: str
        outcome: str


class InvoiceTypes:
    """Public invoice types."""

    BriefFetch = _Types.BriefFetch
    Registered = _Types.Registered


class InvoiceEntityClass:
    """Own the invoice register."""

    def __init__(self, db_adapter: AdapterTypes.DB) -> None:
        """Keep the database."""
        self._db_adapter = db_adapter

    def clear_in(self, session: Session) -> None:
        """Delete every registered invoice inside the caller's transaction."""
        session.execute(delete(InvoiceDB.Invoices))

    def register_in(
        self,
        session: Session,
        run_id: str,
        extraction: ExtractionTypes.InvoiceExtraction,
        *,
        vendor_id: str | None,
        po_number: str | None,
        taxable: Decimal | None,
        outcome: StageTypes.Outcome,
    ) -> int:
        """Add one register row inside the caller's transaction and return its id."""
        row = InvoiceDB.Invoices(
            run_id=run_id,
            vendor_id=vendor_id,
            po_number=po_number,
            invoice_number=extraction.invoice_number,
            number_key=IdentityTypes.number_key(extraction.invoice_number),
            invoice_date=extraction.invoice_date,
            taxable=taxable,
            currency=extraction.currency,
            fingerprint=IdentityTypes.fingerprint(extraction.lines),
            outcome=outcome,
        )
        session.add(row)
        session.flush()
        return row.id

    async def briefs(self, invoice_ids: Collection[int]) -> dict[int, _Types.BriefFetch]:
        """Return the printed number, taxable value and currency of each registered invoice, by id."""
        columns = (
            InvoiceDB.Invoices.id,
            InvoiceDB.Invoices.invoice_number,
            InvoiceDB.Invoices.taxable,
            InvoiceDB.Invoices.currency,
        )
        query = select(*columns).where(InvoiceDB.Invoices.id.in_(invoice_ids))
        rows = await self._db_adapter.run(lambda session: session.execute(query).all())
        return {
            row.id: _Types.BriefFetch(invoice_number=row.invoice_number, taxable=row.taxable, currency=row.currency)
            for row in rows
        }

    async def approved(self, vendor_id: str) -> list[_Types.Registered]:
        """Return the vendor's approved invoices in registration order; held or rejected attempts never count."""
        query = (
            select(InvoiceDB.Invoices)
            .where(InvoiceDB.Invoices.vendor_id == vendor_id, InvoiceDB.Invoices.outcome == "approved")
            .order_by(InvoiceDB.Invoices.id)
        )

        def load(session: Session) -> list[_Types.Registered]:
            return [
                _Types.Registered(
                    run_id=row.run_id,
                    invoice_number=row.invoice_number,
                    number_key=row.number_key,
                    invoice_date=row.invoice_date,
                    taxable=row.taxable,
                    fingerprint=row.fingerprint,
                    outcome=row.outcome,
                )
                for row in session.scalars(query)
            ]

        return await self._db_adapter.run(load)
