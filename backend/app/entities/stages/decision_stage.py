"""
Stage 8, decision: every finding of the run becomes the outcome, its one-sentence summary and, when approved, the
PO update(s).

Any block in the reject set (file rejected, vendor suspended, PO cancelled, exact duplicate) rejects; any other block
needs attention; none approves. The four gates (vendor, bank, PO, amount) restate the blocks per stage for the
reader; the findings alone decide.
"""

from decimal import Decimal
from typing import TYPE_CHECKING

from app.entities._types import AmountTypes, FindingTypes, StageTypes

if TYPE_CHECKING:
    from app.entities.rules_ent import RulesEntityClass


class DecisionStageClass:
    """Decide the outcome of a run from its findings."""

    def __init__(self, rules_entity: RulesEntityClass) -> None:
        """Keep the AP policy (reject codes, reason order)."""
        self._rules = rules_entity

    def run(
        self,
        findings: list[FindingTypes.Finding],
        amounts: StageTypes.AmountCheck | None,
        po_number: str | None,
        *,
        vendor: StageTypes.VendorMatch | None = None,
        bank: StageTypes.BankCheck | None = None,
        order: StageTypes.OrderMatch | None = None,
    ) -> StageTypes.Decision:
        """
        Turn every finding of the run into the outcome, its summary and, when approved, the PO update.

        The four gates (vendor, bank, PO, amount) restate the block findings per stage; the findings alone decide.
        """
        reasons = self._rules.ordered(findings)
        gates = self._gates(vendor, bank, order, amounts)
        blocks = [finding for finding in reasons if finding.severity == "block"]
        if blocks:
            others = len(blocks) - 1
            more = f" {others} more issue{'s' if others > 1 else ''} to review." if others else ""
            outcome: StageTypes.Outcome = (
                "rejected" if blocks[0].code in self._rules.reject_codes else "needs_attention"
            )
            return StageTypes.Decision(
                outcome=outcome, summary=f"{blocks[0].message}{more}", reasons=reasons, gates=gates, update=None
            )

        if amounts is None or po_number is None:
            return StageTypes.Decision(
                outcome="approved", summary="Approved.", reasons=reasons, gates=gates, update=None
            )

        if amounts.parts:
            return self._split(reasons, gates, amounts)

        left = amounts.remaining_before - amounts.invoice_amount
        update = StageTypes.OrderUpdate(
            po_number=po_number,
            amount=amounts.invoice_amount,
            remaining_after=left,
            quantities=self._quantities(amounts.lines),
        )
        inferred = any(reason.code == "PO_INFERRED" for reason in reasons)
        opening = f"Approved against inferred {po_number}" if inferred else "Approved"
        if any(reason.code == "PARTIAL_DELIVERY" for reason in reasons):
            summary = f"{opening} as partial delivery; {AmountTypes.money(left)} remains open on {po_number}."
        elif left < 0:
            summary = (
                f"{opening}; {po_number} is now fully invoiced, {AmountTypes.money(-left)} over its balance and within "
                f"the {AmountTypes.money(amounts.tolerance)} tolerance."
            )
        else:
            summary = f"{opening}; {po_number} is now fully invoiced."

        return StageTypes.Decision(outcome="approved", summary=summary, reasons=reasons, gates=gates, update=update)

    def _split(
        self, reasons: list[FindingTypes.Finding], gates: list[StageTypes.Gate], amounts: StageTypes.AmountCheck
    ) -> StageTypes.Decision:
        """Approve an invoice split between several POs: one update per PO, each for that PO's share."""
        updates = [
            StageTypes.OrderUpdate(
                po_number=part.po_number,
                amount=part.invoice_amount,
                remaining_after=part.remaining_before - part.invoice_amount,
                quantities=self._quantities([line for line in amounts.lines if line.po_number == part.po_number]),
            )
            for part in amounts.parts
        ]
        shares = " and ".join(
            f"{AmountTypes.money(update.amount)} on {update.po_number} "
            + (
                f"({AmountTypes.money(update.remaining_after)} remains open)"
                if update.remaining_after > 0
                else "(now fully invoiced)"
            )
            for update in updates
        )
        return StageTypes.Decision(
            outcome="approved",
            summary=f"Approved and split between {len(updates)} POs: {shares}.",
            reasons=reasons,
            gates=gates,
            update=updates[0],
            others=updates[1:],
        )

    @staticmethod
    def _quantities(lines: list[StageTypes.LineResult]) -> dict[int, Decimal]:
        """Add up the invoiced quantity per PO line number."""
        quantities: dict[int, Decimal] = {}
        for line in lines:
            if line.po_line is not None and line.quantity is not None:
                quantities[line.po_line] = quantities.get(line.po_line, Decimal(0)) + line.quantity

        return quantities

    @staticmethod
    def _gates(
        vendor: StageTypes.VendorMatch | None,
        bank: StageTypes.BankCheck | None,
        order: StageTypes.OrderMatch | None,
        amounts: StageTypes.AmountCheck | None,
    ) -> list[StageTypes.Gate]:
        """Summarise the four checks an approval needs; a gate fails when its stage did not run or raised a block."""
        passes = {
            "vendor": f"{vendor.legal_name} identified with score {vendor.score:.0f} (more than {vendor.needed:.0f} "
            "needed)."
            if vendor
            else "",
            "bank": (
                f"The payee account {bank.invoice_account} is the vendor master's."
                if bank.state == "matched"
                else f"No account printed; payment goes to the master account {bank.master_account}."
            )
            if bank
            else "",
            "po": DecisionStageClass._po_gate(order) if order else "",
            "amount": (
                f"{AmountTypes.money(amounts.invoice_amount)} split between {len(amounts.parts)} POs, each share "
                "within its PO's balance; every line was checked."
                if amounts.parts
                else f"{AmountTypes.money(amounts.invoice_amount)} against "
                f"{AmountTypes.money(amounts.remaining_before)} left on the PO, tolerance "
                f"{AmountTypes.money(amounts.tolerance)}; every line was checked."
            )
            if amounts
            else "",
        }
        stages: dict[
            StageTypes.GateName,
            StageTypes.VendorMatch | StageTypes.BankCheck | StageTypes.OrderMatch | StageTypes.AmountCheck | None,
        ]
        stages = {"vendor": vendor, "bank": bank, "po": order, "amount": amounts}
        gates: list[StageTypes.Gate] = []
        for name, stage in stages.items():
            blocks = [finding.message for finding in stage.findings if finding.severity == "block"] if stage else []
            detail = blocks[0] if blocks else passes[name] if stage else "Not checked; an earlier stage did not finish."
            gates.append(StageTypes.Gate(name=name, passed=stage is not None and not blocks, detail=detail))

        return gates

    @staticmethod
    def _po_gate(order: StageTypes.OrderMatch) -> str:
        """Say how the PO(s) were matched, for the PO gate."""
        numbers = " and ".join(filter(None, [order.po_number, *order.others]))
        if order.method == "explicit":
            return f"{numbers} printed on the invoice; every invoice line was matched to its lines."

        if order.method == "jev":
            return f"{numbers} matched by the invoice lines, with Jev settling the lines the code was not sure about."

        if order.method == "lines":
            return f"{numbers} matched by the invoice lines."

        return f"{numbers} matched with score {order.score:.0f} (more than {order.needed:.0f} needed)."
