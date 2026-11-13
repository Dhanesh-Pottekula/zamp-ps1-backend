"""
Stage 9, persist: one transaction that closes the run, registers the invoice and, on approval, consumes every PO.

Closing comes first and doubles as the reset check: a run a reset already deleted matches no row, so nothing else
is written. Every PO of a split invoice is consumed in this one transaction: all of them, or none.
"""

import time
from typing import TYPE_CHECKING, Any

from app.entities._types import StageTypes

if TYPE_CHECKING:
    from decimal import Decimal

    from sqlalchemy.orm import Session

    from app.adapters import AdapterTypes
    from app.entities._types import ExtractionTypes
    from app.entities.invoice_ent import InvoiceEntityClass
    from app.entities.order_ent import OrderEntityClass
    from app.entities.run_ent import RunEntityClass


class _Helper:
    """Timing."""

    @staticmethod
    def millis(started: float) -> int:
        """Return whole milliseconds since started."""
        return round((time.perf_counter() - started) * 1000)


class PersistStageClass:
    """Register the invoice, consume the approved PO shares and close the run, all in one transaction."""

    def __init__(
        self,
        db_adapter: AdapterTypes.DB,
        *,
        run_entity: RunEntityClass,
        invoice_entity: InvoiceEntityClass,
        order_entity: OrderEntityClass,
    ) -> None:
        """Keep the database, the runs, the invoice register and the POs."""
        self._db_adapter = db_adapter
        self._run_entity = run_entity
        self._invoice_entity = invoice_entity
        self._order_entity = order_entity

    async def run(
        self,
        run_id: str,
        *,
        reading: ExtractionTypes.InvoiceExtraction | None,
        taxable: Decimal | None,
        decision: StageTypes.Decision,
        closing: StageTypes.RunClosing,
    ) -> StageTypes.StepEvent | None:
        """Write everything in one transaction; return the persist step, or None when a reset deleted the run."""
        started = time.perf_counter()
        update = decision.update
        persisted: dict[str, Any] = {
            "registered": reading is not None,
            "po_updated": update.po_number if update else None,
            "remaining_after": f"{update.remaining_after:.2f}" if update else None,
            "others": [
                {"po_updated": other.po_number, "remaining_after": f"{other.remaining_after:.2f}"}
                for other in decision.others
            ],
        }

        def work(session: Session) -> StageTypes.StepEvent | None:
            # Closing first is the reset check: it is this transaction's first write, so it takes SQLite's write
            # lock, and a run a reset already deleted matches no row and writes nothing else.
            if not self._run_entity.close_in(session, run_id, closing):
                return None

            if reading is not None:
                invoice_id = self._invoice_entity.register_in(
                    session,
                    run_id,
                    reading,
                    vendor_id=closing.vendor_id,
                    po_number=closing.po_number,
                    taxable=taxable,
                    outcome=decision.outcome,
                )
                self._run_entity.link_in(session, run_id, invoice_id)

            # Every PO of a split invoice is consumed in this one transaction: all of them, or none.
            for consumed in (update, *decision.others) if update is not None else ():
                self._order_entity.consume_in(session, consumed)

            event = StageTypes.StepEvent(
                run_id=run_id,
                name="persist",
                state="done",
                millis=_Helper.millis(started),
                data=persisted,
                findings=[],
            )
            self._run_entity.record_in(session, event)
            return event

        return await self._db_adapter.run(work)
