"""
Stage 5, duplicate check: was this invoice already approved for the vendor.

The same normalised invoice number is an exact duplicate (rejected); the same taxable value with the same date or
the same line items under a new number is a suspected re-send (held). Only approved invoices count, so a held or
rejected invoice that is corrected and re-run is not blocked by its own earlier attempt. It needs no PO, so it runs
before PO matching: an invoice already paid never reaches Jev.
"""

from typing import TYPE_CHECKING

from app.entities._types import FindingTypes, IdentityTypes, StageTypes

if TYPE_CHECKING:
    from decimal import Decimal

    from app.entities._types import ExtractionTypes
    from app.entities.invoice_ent import InvoiceEntityClass


class DuplicateStageClass:
    """Compare an invoice with the vendor's approved invoices in the register."""

    def __init__(self, invoice_entity: InvoiceEntityClass) -> None:
        """Keep the invoice register."""
        self._invoice_entity = invoice_entity

    async def run(
        self, vendor_id: str, extraction: ExtractionTypes.InvoiceExtraction, taxable: Decimal | None
    ) -> StageTypes.DuplicateCheck:
        """Compare the invoice with the vendor's approved invoices; held or rejected attempts never count."""
        prior = await self._invoice_entity.approved(vendor_id)
        number_key = IdentityTypes.number_key(extraction.invoice_number)
        exact = next((row for row in prior if number_key and row.number_key == number_key), None)
        if exact is not None:
            finding = FindingTypes.Finding(
                code="DUPLICATE_EXACT",
                severity="block",
                message=f"Invoice {extraction.invoice_number} from this vendor was already registered by an earlier "
                f"run ({exact.outcome.replace('_', ' ')}); it must not be paid twice.",
                data={"prior_run": exact.run_id, "invoice_number": extraction.invoice_number or ""},
            )
            return StageTypes.DuplicateCheck(verdict="exact", prior_run=exact.run_id, findings=[finding])

        fingerprint = IdentityTypes.fingerprint(extraction.lines)
        for row in prior:
            same_date = extraction.invoice_date is not None and row.invoice_date == extraction.invoice_date
            same_lines = bool(extraction.lines) and row.fingerprint == fingerprint
            if taxable is None or row.taxable != taxable or not (same_date or same_lines):
                continue

            basis = "date" if same_date else "line items"
            finding = FindingTypes.Finding(
                code="DUPLICATE_SUSPECTED",
                severity="block",
                message=f"Invoice {extraction.invoice_number} has the same vendor, taxable value {taxable:,.2f} and "
                f"{basis} as invoice {row.invoice_number} from an earlier run; it may be a re-send under a new number.",
                data={
                    "prior_run": row.run_id,
                    "prior_number": row.invoice_number or "",
                    "taxable_value": f"{taxable:.2f}",
                    "basis": basis,
                },
            )
            return StageTypes.DuplicateCheck(verdict="suspected", prior_run=row.run_id, findings=[finding])

        clear = FindingTypes.Finding.of(
            "NO_DUPLICATE", "info", "No earlier invoice with this number or these details from this vendor."
        )
        return StageTypes.DuplicateCheck(verdict="none", prior_run=None, findings=[clear])
