"""
Stage 7, amount validation: the invoice against the PO(s) stage 6 settled on.

One PO: the taxable value against the PO balance (one-sided tolerance) and each line against its PO line (quantity
left, unit price, tax rate). Several POs: the invoice is split by its lines, and each PO's share is checked against
that PO in the same way; the split is made only when every line sits on exactly one of the POs.
"""

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING

from app.entities._types import AmountTypes, FindingTypes, StageTypes

if TYPE_CHECKING:
    from app.entities._types import ExtractionTypes
    from app.entities.order_ent import OrderEntityClass
    from app.entities.rules_ent import RulesEntityClass


class AmountsStageClass:
    """Check the invoice amounts and lines against its PO, or split it between its POs and check each share."""

    def __init__(self, rules_entity: RulesEntityClass, order_entity: OrderEntityClass) -> None:
        """Keep the AP policy (tolerance, bands) and the PO entity (snapshots)."""
        self._rules = rules_entity
        self._order_entity = order_entity

    async def snapshots(self, order: StageTypes.OrderMatch) -> list[StageTypes.OrderSnapshot]:
        """Load the PO(s) stage 6 settled on, the first one first."""
        numbers = [order.po_number, *order.others] if order.po_number else []
        found = await asyncio.gather(*(self._order_entity.snapshot(number) for number in numbers))
        return [snapshot for snapshot in found if snapshot is not None]

    @staticmethod
    def skip_reason(
        order: StageTypes.OrderMatch, snapshots: list[StageTypes.OrderSnapshot], taxable: Decimal | None
    ) -> str | None:
        """Return why the stage cannot run (unmatched lines, no PO, or no taxable value), or None."""
        if any(allocation.po_number is None for allocation in order.allocations):
            return "Some invoice lines are not matched to a PO line, so the amounts cannot be checked line by line."

        if not snapshots:
            return "No PO was matched."

        return "There is no taxable value to compare." if taxable is None else None

    def run(
        self,
        order: StageTypes.OrderMatch,
        snapshots: list[StageTypes.OrderSnapshot],
        taxable: Decimal,
        *,
        reading: ExtractionTypes.InvoiceExtraction,
        priced: list[StageTypes.PricedLine],
        pct: Decimal,
        invoice_rate: Decimal | None,
    ) -> StageTypes.AmountCheck:
        """
        Check the invoice against the PO line(s) stage 6 allocated each line to: one PO, or a split between several.

        Each allocation becomes one portion line (the invoice line's description, unit price and tax rate, the
        allocation's quantity), so a line stage 6 put on several PO lines is checked portion by portion.
        """
        lines = [
            priced[allocation.line - 1].model_copy(update={"quantity": allocation.quantity})
            for allocation in order.allocations
        ]
        if len(snapshots) > 1:
            return self.split_check(
                snapshots,
                taxable,
                currency=reading.currency,
                lines=lines,
                mapping=[
                    (allocation.po_number, allocation.po_line)
                    if allocation.po_number is not None and allocation.po_line is not None
                    else None
                    for allocation in order.allocations
                ],
                pct=pct,
                invoice_rate=invoice_rate,
            )

        return self.amount_check(
            snapshots[0],
            taxable,
            currency=reading.currency,
            lines=lines,
            mapping=[allocation.po_line for allocation in order.allocations],
            pct=pct,
            invoice_rate=invoice_rate,
        )

    def amount_check(
        self,
        order: StageTypes.OrderSnapshot,
        taxable: Decimal,
        *,
        currency: str | None,
        lines: list[StageTypes.PricedLine],
        mapping: list[int | None],
        pct: Decimal,
        invoice_rate: Decimal | None,
        noun: str = "taxable value",
    ) -> StageTypes.AmountCheck:
        """
        Compare the taxable value with the PO balance (one-sided tolerance) and check each mapped line.

        noun names the amount in the findings: the taxable value, or one PO's share of a split invoice.
        """
        findings: list[FindingTypes.Finding] = []
        po = order.po_number
        if currency and currency != order.currency:
            findings.append(
                FindingTypes.Finding.of(
                    "CURRENCY_MISMATCH",
                    "block",
                    f"The invoice is in {currency} but {po} is in {order.currency}.",
                    invoice_currency=currency,
                    po_currency=order.currency,
                )
            )

        if not currency:
            findings.append(
                FindingTypes.Finding.of(
                    "CURRENCY_NOT_READ",
                    "warn",
                    f"No currency could be read from the invoice; its amounts were compared with {po} as if they were "
                    f"in {order.currency}. Confirm the currency.",
                    po_currency=order.currency,
                )
            )

        remaining = order.remaining
        tolerance = self._rules.tolerance(remaining, pct, order.currency)
        variance = taxable - remaining
        within = variance <= tolerance
        numbers = {
            "invoice_amount": AmountTypes.plain(taxable),
            "remaining": AmountTypes.plain(remaining),
            "variance": AmountTypes.plain(variance),
            "tolerance": AmountTypes.plain(tolerance),
        }
        if not within:
            findings.append(
                FindingTypes.Finding.of(
                    "OVER_CONSUMPTION",
                    "block",
                    f"The {noun} {AmountTypes.money(taxable)} exceeds the {AmountTypes.money(remaining)} left on {po} "
                    f"by {AmountTypes.money(variance)}, beyond the {AmountTypes.money(tolerance)} tolerance.",
                    **numbers,
                )
            )
        else:
            reach = (
                f"exceeds the {AmountTypes.money(remaining)} left on {po} by {AmountTypes.money(variance)}, within the "
                f"{AmountTypes.money(tolerance)} tolerance"
                if variance > 0
                else f"is within the {AmountTypes.money(remaining)} left on {po} "
                f"(tolerance {AmountTypes.money(tolerance)})"
            )
            findings.append(
                FindingTypes.Finding.of(
                    "AMOUNT_WITHIN_TOLERANCE", "info", f"The {noun} {AmountTypes.money(taxable)} {reach}.", **numbers
                )
            )
            if taxable >= remaining:
                findings.append(
                    FindingTypes.Finding.of(
                        "PO_FULLY_INVOICED",
                        "info",
                        f"This invoice covers the whole remaining balance of {po}.",
                        po_number=po,
                        remaining_after=AmountTypes.plain(remaining - taxable),
                    )
                )
            else:
                findings.append(
                    FindingTypes.Finding.of(
                        "PARTIAL_DELIVERY",
                        "info",
                        f"Partial invoice: {AmountTypes.money(remaining - taxable)} will remain open on {po}.",
                        po_number=po,
                        remaining_after=AmountTypes.plain(remaining - taxable),
                    )
                )

        if not lines or None in mapping:
            findings.append(
                FindingTypes.Finding.of(
                    "HEADER_LEVEL_ONLY",
                    "block",
                    f"The invoice lines could not all be matched to lines of {po}, so only the totals were compared; "
                    "unit price and quantity could not be verified.",
                    po_number=po,
                )
            )
            return StageTypes.AmountCheck(
                basis="taxable_value",
                invoice_amount=taxable,
                remaining_before=remaining,
                variance=variance,
                tolerance=tolerance,
                within=within,
                level="header",
                lines=[],
                findings=findings,
            )

        po_lines = {line.line_no: line for line in order.lines}
        drawn: dict[int, Decimal] = {}
        results: list[StageTypes.LineResult] = []
        for line, line_no in zip(lines, mapping, strict=True):
            po_line = po_lines[line_no or 0]
            consumed = po_line.consumed_qty + drawn.get(po_line.line_no, Decimal(0))
            line_findings = self._line_findings(
                po, line, po_line, consumed=consumed, pct=pct, invoice_rate=invoice_rate
            )
            drawn[po_line.line_no] = drawn.get(po_line.line_no, Decimal(0)) + (line.quantity or Decimal(0))
            findings.extend(line_findings)
            results.append(
                StageTypes.LineResult(
                    description=line.description,
                    po_line=po_line.line_no,
                    quantity=line.quantity,
                    ordered=po_line.quantity,
                    consumed_before=consumed,
                    unit_price=line.unit_price,
                    po_price=po_line.unit_price,
                    ok=not line_findings,
                )
            )

        return StageTypes.AmountCheck(
            basis="taxable_value",
            invoice_amount=taxable,
            remaining_before=remaining,
            variance=variance,
            tolerance=tolerance,
            within=within,
            level="line",
            lines=results,
            findings=findings,
        )

    def split_check(
        self,
        orders: list[StageTypes.OrderSnapshot],
        taxable: Decimal,
        *,
        currency: str | None,
        lines: list[StageTypes.PricedLine],
        mapping: list[tuple[str, int] | None],
        pct: Decimal,
        invoice_rate: Decimal | None,
    ) -> StageTypes.AmountCheck:
        """
        Split an invoice that names several POs by its lines and check each PO's share against that PO.

        The split is made only when every line sits on exactly one of the POs, every PO receives a line and the
        shares add up to the taxable value; otherwise one block finding says why, and nothing is compared by line.
        """
        numbers = " and ".join(order.po_number for order in orders)
        remaining = sum((order.remaining for order in orders), Decimal(0))
        doubt = self._doubt(orders, taxable, lines, mapping)
        if doubt is not None:
            tolerance = sum(
                (self._rules.tolerance(order.remaining, pct, order.currency) for order in orders), Decimal(0)
            )
            finding = FindingTypes.Finding.of(
                "HEADER_LEVEL_ONLY",
                "block",
                f"The invoice names {numbers}, but {doubt}, so its amount cannot be split between them.",
                po_number=orders[0].po_number,
            )
            return StageTypes.AmountCheck(
                basis="taxable_value",
                invoice_amount=taxable,
                remaining_before=remaining,
                variance=taxable - remaining,
                tolerance=tolerance,
                within=taxable - remaining <= tolerance,
                level="header",
                lines=[],
                findings=[finding],
            )

        checks: list[StageTypes.AmountCheck] = []
        for order in orders:
            own = [
                (line, target[1])
                for line, target in zip(lines, mapping, strict=True)
                if target is not None and target[0] == order.po_number
            ]
            share = sum(
                ((line.quantity or Decimal(0)) * (line.unit_price or Decimal(0)) for line, _ in own), Decimal(0)
            )
            checks.append(
                self.amount_check(
                    order,
                    share.quantize(AmountTypes.CENT),
                    currency=currency,
                    lines=[line for line, _ in own],
                    mapping=[row for _, row in own],
                    pct=pct,
                    invoice_rate=invoice_rate,
                    noun="invoice share",
                )
            )

        # The currency is read once for the whole invoice, so "not read" is said once, not once per PO.
        findings = [
            finding
            for index, check in enumerate(checks)
            for finding in check.findings
            if index == 0 or finding.code != "CURRENCY_NOT_READ"
        ]
        return StageTypes.AmountCheck(
            basis="taxable_value",
            invoice_amount=taxable,
            remaining_before=remaining,
            variance=taxable - remaining,
            tolerance=sum((check.tolerance for check in checks), Decimal(0)),
            within=all(check.within for check in checks),
            level="line",
            lines=[
                line.model_copy(update={"po_number": order.po_number})
                for order, check in zip(orders, checks, strict=True)
                for line in check.lines
            ],
            parts=[
                StageTypes.AmountPart(
                    po_number=order.po_number,
                    invoice_amount=check.invoice_amount,
                    remaining_before=check.remaining_before,
                    variance=check.variance,
                    tolerance=check.tolerance,
                    within=check.within,
                )
                for order, check in zip(orders, checks, strict=True)
            ],
            findings=findings,
        )

    def _doubt(
        self,
        orders: list[StageTypes.OrderSnapshot],
        taxable: Decimal,
        lines: list[StageTypes.PricedLine],
        mapping: list[tuple[str, int] | None],
    ) -> str | None:
        """Return why an invoice cannot be split between its POs with certainty, or None when it can."""
        for line, target in zip(lines, mapping, strict=True):
            if target is None:
                return f"the line '{line.description}' is not on exactly one of them"

            if line.quantity is None or line.unit_price is None:
                return f"the line '{line.description}' has no quantity or unit price"

        used = {target[0] for target in mapping if target is not None}
        unused = next((order.po_number for order in orders if order.po_number not in used), None)
        if unused is not None:
            return f"no line of the invoice is on {unused}"

        total = sum(((line.quantity or Decimal(0)) * (line.unit_price or Decimal(0)) for line in lines), Decimal(0))
        if abs(total - taxable) > self._rules.sum_band:
            return f"its lines add up to {AmountTypes.money(total)}, not the taxable value {AmountTypes.money(taxable)}"

        return None

    def _line_findings(
        self,
        po: str,
        line: StageTypes.PricedLine,
        po_line: StageTypes.OrderLine,
        *,
        consumed: Decimal,
        pct: Decimal,
        invoice_rate: Decimal | None,
    ) -> list[FindingTypes.Finding]:
        """Check one mapped line's quantity, unit price and tax rate against its PO line."""
        findings: list[FindingTypes.Finding] = []
        label = f"line {po_line.line_no} of {po} ({po_line.description})"
        if line.quantity is not None and consumed + line.quantity > po_line.quantity:
            findings.append(
                FindingTypes.Finding.of(
                    "QTY_EXCEEDS_ORDERED",
                    "block",
                    f"{consumed + line.quantity:f} units would be invoiced against {label}, but only "
                    f"{po_line.quantity:f} were ordered ({consumed:f} already invoiced).",
                    po_line=str(po_line.line_no),
                    quantity=f"{line.quantity:f}",
                    consumed=f"{consumed:f}",
                    ordered=f"{po_line.quantity:f}",
                )
            )

        if line.unit_price is not None and AmountTypes.percent_apart(line.unit_price, po_line.unit_price) > pct:
            findings.append(
                FindingTypes.Finding.of(
                    "UNIT_PRICE_MISMATCH",
                    "block",
                    f"The unit price {AmountTypes.money(line.unit_price)} differs from the "
                    f"{AmountTypes.money(po_line.unit_price)} agreed on {label} by more than {pct:f} percent.",
                    po_line=str(po_line.line_no),
                    unit_price=AmountTypes.plain(line.unit_price),
                    po_price=AmountTypes.plain(po_line.unit_price),
                )
            )

        rate = line.tax_rate_pct if line.tax_rate_pct is not None else invoice_rate
        if rate is not None and abs(rate - po_line.tax_rate_pct) > self._rules.rate_band:
            findings.append(
                FindingTypes.Finding.of(
                    "TAX_RATE_MISMATCH",
                    "block",
                    f"The tax rate {rate:f}% differs from the {po_line.tax_rate_pct:f}% expected on {label}.",
                    po_line=str(po_line.line_no),
                    rate_pct=f"{rate:f}",
                    po_rate_pct=f"{po_line.tax_rate_pct:f}",
                )
            )

        return findings
