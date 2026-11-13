"""
Stage 2, validation: the taxable value and tax rate later stages compare, and the arithmetic of the reading.

Flags missing invoice numbers, totals and dates, ambiguous or future dates, malformed tax IDs, line sums and tax sums
that do not add up, and non-positive amounts. Also prices each line on the pre-tax basis POs are stored in.
"""

import re
from decimal import Decimal
from typing import TYPE_CHECKING

from app.entities._types import AmountTypes, FindingTypes, StageTypes

if TYPE_CHECKING:
    from datetime import date

    from app.entities._types import ExtractionTypes
    from app.entities.rules_ent import RulesEntityClass


class ValidationStageClass:
    """Validate a reading and derive the values later stages compare."""

    def __init__(self, rules_entity: RulesEntityClass) -> None:
        """Keep the AP policy (GSTIN shape, the sum band)."""
        self._rules = rules_entity

    def run(self, extraction: ExtractionTypes.InvoiceExtraction, today: date) -> StageTypes.ValidationResult:
        """Derive the taxable value and tax rate, and flag missing or inconsistent printed values."""
        tax_amounts = [tax.amount for tax in extraction.tax_lines if tax.amount is not None]
        tax_rates = [tax.rate_pct for tax in extraction.tax_lines if tax.rate_pct is not None]
        line_rates = {line.tax_rate_pct for line in extraction.lines if line.tax_rate_pct is not None}
        sole_rate = next(iter(line_rates)) if len(line_rates) == 1 else None
        # Tax lines at one rate split a single base (CGST 9 + SGST 9 = 18). Lines at different rates (VAT 19 and 7)
        # tax different lines, so their sum is no rate at all and the invoice has no single rate to fall back on.
        one_base = len(set(tax_rates)) <= 1
        known_rate = (sum(tax_rates, Decimal(0)) if one_base else None) if tax_rates else sole_rate
        subtotal, total = extraction.subtotal, extraction.total

        taxable: Decimal | None = subtotal
        derived = False
        if subtotal is None and total is not None and tax_amounts:
            taxable, derived = total - sum(tax_amounts, Decimal(0)), True
        elif subtotal is None and total is not None and known_rate is not None:
            taxable, derived = (total / (1 + known_rate / AmountTypes.HUNDRED)).quantize(AmountTypes.CENT), True

        tax_total = sum(tax_amounts, Decimal(0)) if tax_amounts else None
        if tax_total is None and total is not None and taxable is not None:
            tax_total = total - taxable

        tax_rate = sum(tax_rates, Decimal(0)) if tax_rates and one_base else None
        if tax_rate is None and one_base and tax_total is not None and taxable:
            tax_rate = (tax_total / taxable * AmountTypes.HUNDRED).quantize(AmountTypes.CENT)

        findings = self._header_findings(extraction, today)
        findings.extend(self._amount_findings(extraction, taxable, derived=derived))
        return StageTypes.ValidationResult(
            taxable_value=taxable, tax_total=tax_total, tax_rate_pct=tax_rate, derived=derived, findings=findings
        )

    def _header_findings(
        self, extraction: ExtractionTypes.InvoiceExtraction, today: date
    ) -> list[FindingTypes.Finding]:
        """Flag a missing invoice number, a missing or future date and a malformed GSTIN."""
        findings: list[FindingTypes.Finding] = []
        if not extraction.invoice_number:
            message = "No invoice number could be read, so the invoice cannot be approved or checked for duplicates."
            findings.append(FindingTypes.Finding.of("MISSING_INVOICE_NUMBER", "block", message))

        if extraction.invoice_date is None:
            findings.append(FindingTypes.Finding.of("MISSING_DATE", "warn", "No invoice date could be read."))
        elif extraction.invoice_date > today:
            printed = extraction.invoice_date.isoformat()
            message = f"The invoice is dated {printed}, which is in the future."
            findings.append(FindingTypes.Finding.of("DATE_IN_FUTURE", "warn", message, invoice_date=printed))

        read_as = {"invoice_date": extraction.invoice_date, "due_date": extraction.due_date}
        quotes = {source.field: source.text for source in extraction.sources}
        for field in extraction.ambiguous_dates:
            value = read_as.get(field)
            shown = value.strftime("%d %B %Y").lstrip("0") if value else "unknown"
            label = field.replace("_", " ")
            message = (
                f"The {label} is printed as '{quotes.get(field, '')}', which reads as day/month or month/day and the "
                f"document does not show which; it was read day first as {shown}. Confirm it."
            )
            findings.append(
                FindingTypes.Finding.of("DATE_AMBIGUOUS", "warn", message, field=field, read_as=str(value or ""))
            )

        # A GSTIN is a two-digit state code, a PAN (five letters first) and three more characters; other 15-character
        # IDs, such as an Indonesian NPWP, are never held to it.
        tax_id = re.sub(r"[^0-9A-Z]", "", (extraction.supplier.tax_id or "").upper())
        if (
            len(tax_id) == 15
            and tax_id[:2].isdigit()
            and tax_id[2:7].isalpha()
            and not self._rules.gstin_shaped(tax_id)
        ):
            message = f"The supplier tax ID {tax_id} does not have the shape of a valid GSTIN."
            findings.append(FindingTypes.Finding.of("TAX_ID_FORMAT_INVALID", "warn", message, tax_id=tax_id))

        return findings

    def _amount_findings(
        self, extraction: ExtractionTypes.InvoiceExtraction, taxable: Decimal | None, *, derived: bool
    ) -> list[FindingTypes.Finding]:
        """Flag a derived or missing taxable value; line or tax sums that do not add up block (misread or altered)."""
        findings: list[FindingTypes.Finding] = []
        subtotal, total = extraction.subtotal, extraction.total
        if derived and taxable is not None and total is not None:
            message = (
                f"No pre-tax subtotal is printed; the taxable value {AmountTypes.money(taxable)} was derived from the "
                f"total {AmountTypes.money(total)}."
            )
            numbers = {"taxable_value": AmountTypes.plain(taxable), "total": AmountTypes.plain(total)}
            findings.append(FindingTypes.Finding.of("TAXABLE_VALUE_DERIVED", "info", message, **numbers))

        if total is None or taxable is None:
            message = "The invoice total or taxable value could not be read, so amounts cannot be checked."
            findings.append(FindingTypes.Finding.of("MISSING_TOTAL", "block", message))

        negative_lines = [
            line
            for line in extraction.lines
            if (line.quantity is not None and line.quantity < 0)
            or (line.line_amount is not None and line.line_amount < 0)
        ]
        if (taxable is not None and taxable <= 0) or negative_lines:
            shown = AmountTypes.money(taxable) if taxable is not None else "unknown"
            message = (
                f"The taxable value is {shown} and {len(negative_lines)} line(s) are negative; a credit note or a "
                "misread amount is never processed or consumed automatically."
            )
            numbers = {
                "taxable_value": AmountTypes.plain(taxable) if taxable is not None else "",
                "negative_lines": str(len(negative_lines)),
            }
            findings.append(FindingTypes.Finding.of("NON_POSITIVE_AMOUNT", "block", message, **numbers))

        # Gross (tax-inclusive) lines add up to the total; net lines add up to the taxable value.
        line_amounts = [line.line_amount for line in extraction.lines if line.line_amount is not None]
        line_sum = sum(line_amounts, Decimal(0))
        inclusive = extraction.prices_include_tax
        target, target_key = (total, "total") if inclusive else (None if derived else taxable, "taxable_value")
        if target is not None and line_amounts and abs(line_sum - target) > self._rules.sum_band:
            message = (
                f"The line amounts add up to {AmountTypes.money(line_sum)} but the {target_key.replace('_', ' ')} is "
                f"{AmountTypes.money(target)}."
            )
            numbers = {"line_sum": AmountTypes.plain(line_sum), target_key: AmountTypes.plain(target)}
            findings.append(FindingTypes.Finding.of("LINE_SUM_MISMATCH", "block", message, **numbers))

        tax = sum((line.amount for line in extraction.tax_lines if line.amount is not None), Decimal(0))
        has_tax = any(line.amount is not None for line in extraction.tax_lines)
        if (
            subtotal is not None
            and total is not None
            and has_tax
            and abs(subtotal + tax - total) > self._rules.sum_band
        ):
            message = (
                f"Subtotal {AmountTypes.money(subtotal)} plus tax {AmountTypes.money(tax)} is "
                f"{AmountTypes.money(subtotal + tax)}, but the printed total is {AmountTypes.money(total)}."
            )
            numbers = {
                "subtotal": AmountTypes.plain(subtotal),
                "tax": AmountTypes.plain(tax),
                "total": AmountTypes.plain(total),
            }
            findings.append(FindingTypes.Finding.of("TAX_SUM_MISMATCH", "block", message, **numbers))

        return findings

    def priced_lines(
        self, extraction: ExtractionTypes.InvoiceExtraction, validation: StageTypes.ValidationResult
    ) -> list[StageTypes.PricedLine]:
        """Return the invoice lines with unit prices on the pre-tax basis POs are stored in."""
        return [
            StageTypes.PricedLine(
                description=line.description or "",
                quantity=line.quantity,
                unit_price=AmountTypes.net_price(
                    line.unit_price,
                    line.tax_rate_pct if line.tax_rate_pct is not None else validation.tax_rate_pct,
                    extraction.prices_include_tax,
                ),
                tax_rate_pct=line.tax_rate_pct,
            )
            for line in extraction.lines
        ]
