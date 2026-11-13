"""
Purchase orders: creation, balances, mapping invoice lines to PO lines, and consumption.

Matching an invoice to its PO is stage 6 (stages/po_stage.py); it reads POs and maps lines through this entity, and
the amount stage maps lines with the same rules.

A PO's consumed amount is a header column, separate from line quantities, because a header-level approval
consumes amount without quantities.
"""

import asyncio
from datetime import date  # noqa: TC003 - SQLAlchemy and pydantic resolve column and field types at runtime
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel
from sqlalchemy import Date, ForeignKey, Integer, String, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters import Base, DecimalText
from app.entities._types import IdentityTypes, MasterTypes, StageTypes

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.adapters import AdapterTypes
    from app.entities.vendor_ent import VendorEntityClass


class OrderDB:
    """Purchase order tables."""

    class Orders(Base):
        """A PO header with its pre-tax total and the amount invoiced against it so far."""

        __tablename__ = "orders"

        po_number: Mapped[str] = mapped_column(String(16), primary_key=True)
        vendor_id: Mapped[str] = mapped_column(String(16), index=True)
        vendor_name: Mapped[str] = mapped_column(String(200))
        po_date: Mapped[date] = mapped_column(Date)
        currency: Mapped[str] = mapped_column(String(3))
        total: Mapped[Decimal] = mapped_column(DecimalText)
        consumed: Mapped[Decimal] = mapped_column(DecimalText)
        status: Mapped[str] = mapped_column(String(20))

    class OrderLines(Base):
        """One PO line with the quantity invoiced against it so far."""

        __tablename__ = "order_lines"

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        po_number: Mapped[str] = mapped_column(ForeignKey("orders.po_number"), index=True)
        line_no: Mapped[int] = mapped_column(Integer)
        description: Mapped[str] = mapped_column(String(300))
        quantity: Mapped[Decimal] = mapped_column(DecimalText)
        unit: Mapped[str] = mapped_column(String(16))
        unit_price: Mapped[Decimal] = mapped_column(DecimalText)
        tax_rate_pct: Mapped[Decimal] = mapped_column(DecimalText)
        consumed_qty: Mapped[Decimal] = mapped_column(DecimalText)


class _Types:
    """Seed row shapes and a new PO as entered."""

    class OrderSeed(BaseModel):
        """One row of purchase_orders.csv."""

        po_number: str
        vendor_id: str
        po_date: date
        currency: str
        cancelled: bool

    class LineSeed(BaseModel):
        """One row of po_lines.csv."""

        po_number: str
        line_no: int
        description: str
        quantity: Decimal
        unit: str
        unit_price: Decimal
        tax_rate_pct: Decimal
        consumed_qty: Decimal

    class DraftLine(BaseModel):
        """One line of a new PO as entered."""

        description: str
        quantity: Decimal
        unit: str
        unit_price: Decimal
        tax_rate_pct: Decimal | None

    class Draft(BaseModel):
        """A new PO as entered; its number, currency, total and status are derived on creation."""

        vendor_id: str
        po_date: date
        lines: list[_Types.DraftLine]


class OrderTypes:
    """Public order types."""

    Draft = _Types.Draft


class _Helper:
    """Status rules, snapshots and the new-PO checks."""

    HUNDRED: Final = Decimal(100)
    INDIAN_GST: Final = Decimal(18)
    MAX_LINES: Final = 20

    @staticmethod
    def status(consumed: Decimal, total: Decimal, *, cancelled: bool) -> StageTypes.OrderStatus:
        """Return the PO status that follows from its consumption."""
        if cancelled:
            return "cancelled"

        if consumed <= 0:
            return "open"

        return "fully_invoiced" if consumed >= total else "partially_invoiced"

    @staticmethod
    def problem(lines: list[_Types.DraftLine]) -> str | None:
        """Return the first reason the lines cannot make a PO, or None."""
        if not 1 <= len(lines) <= _Helper.MAX_LINES:
            return f"A purchase order needs between 1 and {_Helper.MAX_LINES} lines."

        return next(
            (
                f"Line {number} {needs}."
                for number, line in enumerate(lines, start=1)
                for failed, needs in (
                    (not line.description.strip(), "needs a description"),
                    (line.quantity <= 0, "needs a quantity above zero"),
                    (line.unit_price <= 0, "needs a unit price above zero"),
                    (
                        line.tax_rate_pct is not None and not 0 <= line.tax_rate_pct <= _Helper.HUNDRED,
                        "needs a tax rate from 0 to 100 percent",
                    ),
                )
                if failed
            ),
            None,
        )

    @staticmethod
    def snapshot(order: OrderDB.Orders, lines: list[OrderDB.OrderLines]) -> StageTypes.OrderSnapshot:
        """Turn the ORM rows into the shared PO snapshot."""
        return StageTypes.OrderSnapshot(
            po_number=order.po_number,
            vendor_id=order.vendor_id,
            vendor_name=order.vendor_name,
            po_date=order.po_date,
            currency=order.currency,
            status=order.status,
            total=order.total,
            consumed=order.consumed,
            remaining=order.total - order.consumed,
            lines=[
                StageTypes.OrderLine(
                    line_no=line.line_no,
                    description=line.description,
                    quantity=line.quantity,
                    unit=line.unit,
                    unit_price=line.unit_price,
                    tax_rate_pct=line.tax_rate_pct,
                    consumed_qty=line.consumed_qty,
                )
                for line in sorted(lines, key=lambda row: row.line_no)
            ],
        )


class OrderEntityClass:
    """Own purchase orders and their balances."""

    def __init__(
        self,
        db_adapter: AdapterTypes.DB,
        match_adapter: AdapterTypes.Match,
        *,
        vendor_entity: VendorEntityClass,
    ) -> None:
        """Keep the database, the fuzzy matcher (line scoring), the vendor master and a creation lock."""
        self._db_adapter = db_adapter
        self._match_adapter = match_adapter
        self._vendor_entity = vendor_entity
        self._create_lock = asyncio.Lock()

    def reset_in(
        self,
        session: Session,
        order_rows: list[dict[str, str]],
        line_rows: list[dict[str, str]],
        vendor_names: dict[str, str],
    ) -> None:
        """Replace every PO with the seed rows inside the caller's transaction; consumption follows the line seed."""
        session.execute(delete(OrderDB.OrderLines))
        session.execute(delete(OrderDB.Orders))
        lines = [_Types.LineSeed.model_validate(row) for row in line_rows]
        for order in (_Types.OrderSeed.model_validate(row) for row in order_rows):
            own = [line for line in lines if line.po_number == order.po_number]
            total = sum((line.quantity * line.unit_price for line in own), Decimal(0))
            consumed = sum((line.consumed_qty * line.unit_price for line in own), Decimal(0))
            session.add(
                OrderDB.Orders(
                    po_number=order.po_number,
                    vendor_id=order.vendor_id,
                    vendor_name=vendor_names.get(order.vendor_id, order.vendor_id),
                    po_date=order.po_date,
                    currency=order.currency,
                    total=total,
                    consumed=consumed,
                    status=_Helper.status(consumed, total, cancelled=order.cancelled),
                )
            )

        session.flush()
        session.add_all(OrderDB.OrderLines(**line.model_dump()) for line in lines)

    async def create(self, draft: _Types.Draft) -> StageTypes.OrderSnapshot:
        """
        Raise an open PO in the vendor's currency, numbered PO-<year of its date>-<next sequence across all POs>.

        Raises:
            MasterTypes.MasterError: invalid for a bad line; unknown for an unknown vendor; conflict for a suspended
                vendor.

        """
        problem = _Helper.problem(draft.lines)
        if problem is not None:
            raise MasterTypes.MasterError("invalid", problem)

        vendor = await self._vendor_entity.vendor(draft.vendor_id)
        if vendor is None:
            raise MasterTypes.MasterError("unknown", f"No vendor has the id {draft.vendor_id}.")

        if vendor.status == "suspended":
            message = (
                f"No purchase order can be raised for a suspended vendor, and {vendor.legal_name} "
                f"({vendor.vendor_id}) is suspended."
            )
            raise MasterTypes.MasterError("conflict", message)

        # GST at 18 percent is the usual rate in India; elsewhere there is no safe default, so the rate must be given.
        missing = next((number for number, line in enumerate(draft.lines, start=1) if line.tax_rate_pct is None), None)
        if missing is not None and vendor.country != "IN":
            message = f"Line {missing} needs a tax rate for a {vendor.country} vendor; enter 0 when no tax applies."
            raise MasterTypes.MasterError("invalid", message)

        rates = [line.tax_rate_pct if line.tax_rate_pct is not None else _Helper.INDIAN_GST for line in draft.lines]

        def work(session: Session) -> StageTypes.OrderSnapshot:
            numbers = session.scalars(select(OrderDB.Orders.po_number))
            sequence = max(
                (int(found.group(2)) for number in numbers if (found := IdentityTypes.PO_PATTERN.fullmatch(number))),
                default=0,
            )
            po_number = f"PO-{draft.po_date.year}-{sequence + 1:04d}"
            order = OrderDB.Orders(
                po_number=po_number,
                vendor_id=vendor.vendor_id,
                vendor_name=vendor.legal_name,
                po_date=draft.po_date,
                currency=vendor.currency,
                total=sum((line.quantity * line.unit_price for line in draft.lines), Decimal(0)),
                consumed=Decimal(0),
                status="open",
            )
            lines = [
                OrderDB.OrderLines(
                    po_number=po_number,
                    line_no=number,
                    description=line.description.strip(),
                    quantity=line.quantity,
                    unit=line.unit.strip(),
                    unit_price=line.unit_price,
                    tax_rate_pct=rate,
                    consumed_qty=Decimal(0),
                )
                for number, (line, rate) in enumerate(zip(draft.lines, rates, strict=True), start=1)
            ]
            session.add(order)
            session.flush()
            session.add_all(lines)
            return _Helper.snapshot(order, lines)

        async with self._create_lock:
            return await self._db_adapter.run(work)

    async def snapshots(self, vendor_id: str | None = None) -> list[StageTypes.OrderSnapshot]:
        """Return every PO (or one vendor's) with its balance and lines, ordered by PO number."""

        def load(session: Session) -> list[StageTypes.OrderSnapshot]:
            query = select(OrderDB.Orders).order_by(OrderDB.Orders.po_number)
            orders = session.scalars(query if vendor_id is None else query.where(OrderDB.Orders.vendor_id == vendor_id))
            lines = list(session.scalars(select(OrderDB.OrderLines)))
            return [
                _Helper.snapshot(order, [line for line in lines if line.po_number == order.po_number])
                for order in orders
            ]

        return await self._db_adapter.run(load)

    async def snapshot(self, po_number: str) -> StageTypes.OrderSnapshot | None:
        """Return one PO with its balance and lines, or None when it does not exist."""

        def load(session: Session) -> StageTypes.OrderSnapshot | None:
            order = session.get(OrderDB.Orders, po_number)
            if order is None:
                return None

            lines = session.scalars(select(OrderDB.OrderLines).where(OrderDB.OrderLines.po_number == po_number))
            return _Helper.snapshot(order, list(lines))

        return await self._db_adapter.run(load)

    def same_item(self, left: str, right: str) -> bool:
        """Return whether two PO line descriptions name the same item (near-identical text, same numbers)."""
        return self._match_adapter.same_item(left, right)

    def score_line(
        self, pool: list[StageTypes.OrderSnapshot], line: StageTypes.PricedLine
    ) -> list[StageTypes.LineCandidate]:
        """
        Score one invoice line against every line of every pool PO by description (0 to 100), best first.

        Ties keep the oldest PO first, then the PO number and the line number. A line with no description scores 0.
        """
        scored = [
            StageTypes.LineCandidate(
                po_number=order.po_number,
                po_line=po_line.line_no,
                po_date=order.po_date,
                description=po_line.description,
                score=self._match_adapter.ratio(line.description, po_line.description) if line.description else 0.0,
                coverage=self._match_adapter.coverage(line.description, po_line.description)
                if line.description
                else 0.0,
                left=po_line.quantity - po_line.consumed_qty,
            )
            for order in pool
            for po_line in order.lines
        ]
        return sorted(scored, key=lambda item: (-item.score, item.po_date, item.po_number, item.po_line))

    def consume_in(self, session: Session, update: StageTypes.OrderUpdate) -> None:
        """
        Add an approved invoice's amount and line quantities to its PO inside the caller's transaction.

        Raises:
            ValueError: the amount is not positive or a quantity is negative; a credit is never consumed.

        """
        if update.amount <= 0 or any(quantity < 0 for quantity in update.quantities.values()):
            message = f"Refusing to consume {update.amount} and quantities {update.quantities} from {update.po_number}."
            raise ValueError(message)

        order = session.get_one(OrderDB.Orders, update.po_number)
        order.consumed += update.amount
        order.status = _Helper.status(order.consumed, order.total, cancelled=order.status == "cancelled")
        for line in session.scalars(select(OrderDB.OrderLines).where(OrderDB.OrderLines.po_number == update.po_number)):
            line.consumed_qty += update.quantities.get(line.line_no, Decimal(0))
