"""
Stage 6, PO matching by lines: which PO lines (and so which POs) the invoice bills. One rule for any number of POs.

1. The pool: the PO numbers printed on the invoice (each must exist, belong to the vendor and not be cancelled), or,
   when none is printed, the vendor's open POs dated on or before the invoice with something left (newest first).
2. Every invoice line is scored against every line of every pool PO by description. The code is sure of a line only
   when its best score is above the sure threshold, the match also covers most of the PO description (so "Monitor"
   is not taken for "Monitor arm"), and no other pool line scores at or above the candidate cut-off.
3. Jev decides every line the code is not sure about, in one request with the whole invoice and every pool PO
   (po_jev.py): keep the code's suggestion, choose other PO lines (on any pool PO), several, or none.
4. Each settled line becomes allocations (PO, PO line, quantity); a line on several PO lines fills the oldest PO
   first. The POs used are the POs the lines land on. Amounts never choose a PO: stage 7 checks them afterwards.
"""

from decimal import Decimal
from typing import TYPE_CHECKING, Final

from app.entities._types import FindingTypes, IdentityTypes, StageTypes

if TYPE_CHECKING:
    from app.entities._types import ExtractionTypes
    from app.entities.order_ent import OrderEntityClass
    from app.entities.rules_ent import RulesEntityClass
    from app.entities.stages.po_jev import PoJevClass


class _Helper:
    """PO reference parsing and the pool rule."""

    SEARCHABLE: Final = frozenset({"open", "partially_invoiced"})

    @staticmethod
    def references(text: str | None) -> list[str]:
        """Return every distinct PO number printed in the reference text, in printed order, as PO-YYYY-NNNN."""
        printed = text or ""
        found = sorted(
            (*IdentityTypes.PO_PATTERN.finditer(printed), *IdentityTypes.BARE_PATTERN.finditer(printed)),
            key=lambda match: match.start(),
        )
        return list(dict.fromkeys(f"PO-{match.group(1)}-{match.group(2)}" for match in found))

    @staticmethod
    def reference(text: str | None, descriptions: list[str]) -> str | None:
        """Find a PO number in the reference text, then in line descriptions; return it as PO-YYYY-NNNN."""
        printed = text or ""
        match = IdentityTypes.PO_PATTERN.search(printed) or IdentityTypes.BARE_PATTERN.search(printed)
        if match is None and not IdentityTypes.PO_LIKE.search(printed):
            match = next((found for line in descriptions if (found := IdentityTypes.PO_PATTERN.search(line))), None)

        return f"PO-{match.group(1)}-{match.group(2)}" if match else None


class PoStageClass:
    """Match the invoice lines to the lines of the pool POs; the code decides sure lines, Jev the rest."""

    def __init__(
        self,
        order_entity: OrderEntityClass,
        jev: PoJevClass,
        *,
        rules_entity: RulesEntityClass,
        pool_max: int,
        pass_score: float,
    ) -> None:
        """Keep the PO entity, Jev, the line thresholds, the pool size and the pass score shown on the result."""
        self._order_entity = order_entity
        self._jev = jev
        self._rules = rules_entity
        self._pool_max: Final = pool_max
        self._pass_score: Final = pass_score

    async def run(
        self,
        reading: ExtractionTypes.InvoiceExtraction,
        vendor: StageTypes.VendorMatch,
        priced: list[StageTypes.PricedLine],
        *,
        taxable: Decimal | None,
        earlier: list[FindingTypes.Finding],
    ) -> StageTypes.OrderMatch:
        """
        Build the pool, match every line, let Jev decide the lines the code is not sure about, and allocate.

        earlier holds the findings of the stages before this one: when one of them blocks, the invoice is held
        whatever it bills, so Jev is not asked and the lines the code is not sure about stay unmatched.
        """
        pool, printed, refusals = await self.pool(reading, priced, vendor.vendor_id)
        if refusals:
            return self._refused(refusals, pool)

        plans = self.plan(pool, priced)
        decisions: dict[int, tuple[list[str] | None, dict[str, float]]] = {}
        assist: StageTypes.AssistResult | None = None
        asking = any(not item.sure and item.ask for item in plans)
        if asking and vendor.vendor_id is not None and not any(item.severity == "block" for item in earlier):
            decisions, assist = await self._jev.decide(
                reading, vendor_name=vendor.legal_name, taxable=taxable, pool=pool, printed=printed, plans=plans
            )

        # What earlier invoice lines already took from each PO line, so a later split sees what is really left.
        drawn: dict[str, Decimal] = {}
        allocations: list[StageTypes.LineAllocation] = []
        for item in plans:
            settled = self._settle(item, decisions, drawn)
            for allocation in settled:
                if allocation.po_number is not None and allocation.quantity is not None:
                    key = f"{allocation.po_number}/{allocation.po_line}"
                    drawn[key] = drawn.get(key, Decimal(0)) + allocation.quantity
            allocations.extend(settled)
        return self._result(pool, printed, allocations, assist)

    async def pool(
        self,
        reading: ExtractionTypes.InvoiceExtraction,
        priced: list[StageTypes.PricedLine],
        vendor_id: str | None,
    ) -> tuple[list[StageTypes.OrderSnapshot], list[str], list[FindingTypes.Finding]]:
        """Return the pool, the printed PO numbers and any refusal; a refusal leaves the invoice without a pool."""
        reference = reading.po_reference_text
        printed = _Helper.references(reference)
        if not printed:
            single = _Helper.reference(reference, [line.description for line in priced])
            printed = [single] if single else []

        if printed:
            orders = [await self._order_entity.snapshot(number) for number in printed]
            refusals = [
                refusal
                for number, order in zip(printed, orders, strict=True)
                if (refusal := self._refusal(number, order, vendor_id)) is not None
            ]
            return [order for order in orders if order is not None], printed, refusals

        # A PO-like reference that is mistyped must be checked by a person, never replaced by another PO.
        if reference and IdentityTypes.PO_LIKE.search(reference):
            finding = FindingTypes.Finding.of(
                "PO_NOT_FOUND",
                "block",
                f"The order reference '{reference}' does not contain a valid PO number (PO-YYYY-NNNN); confirm it "
                "with the vendor.",
                reference=reference,
                cause="bad_reference",
            )
            return [], [], [finding]

        if vendor_id is None:
            finding = FindingTypes.Finding.of(
                "PO_NOT_FOUND",
                "block",
                "No PO number is printed and the vendor is unknown, so no PO can be matched.",
                cause="no_vendor",
            )
            return [], [], [finding]

        if not priced:
            finding = FindingTypes.Finding.of(
                "PO_NOT_FOUND",
                "block",
                "No PO number is printed and the invoice has no lines to match.",
                cause="no_lines",
            )
            return [], [], [finding]

        invoice_date = reading.invoice_date
        orders = [
            order
            for order in await self._order_entity.snapshots(vendor_id)
            if order.status in _Helper.SEARCHABLE
            and (invoice_date is None or order.po_date <= invoice_date)
            and any(line.quantity - line.consumed_qty > 0 for line in order.lines)
        ]
        orders.sort(key=lambda order: (order.po_date, order.po_number), reverse=True)
        if not orders:
            finding = FindingTypes.Finding.of(
                "PO_NOT_FOUND",
                "block",
                "No PO number is printed and this vendor has no open PO with anything left to invoice.",
                cause="no_open_po",
            )
            return [], [], [finding]

        return orders[: self._pool_max], [], []

    def plan(
        self, pool: list[StageTypes.OrderSnapshot], priced: list[StageTypes.PricedLine]
    ) -> list[StageTypes.LinePlan]:
        """Score every invoice line against the pool and say whether the code is sure of it."""
        plans: list[StageTypes.LinePlan] = []
        for index, line in enumerate(priced, start=1):
            scored = self._order_entity.score_line(pool, line)
            suggestion = scored[0] if scored else None
            rivals = [item for item in scored[1:] if item.score >= self._rules.line_candidate_at]
            sure = (
                suggestion is not None
                and suggestion.score > self._rules.line_sure_above
                and suggestion.coverage >= self._rules.line_coverage_at
                and not rivals
            )
            plans.append(
                StageTypes.LinePlan(
                    line=index,
                    description=line.description,
                    quantity=line.quantity,
                    sure=sure,
                    suggestion=suggestion,
                    rivals=rivals,
                    ask=[item for item in scored if item.left > 0],
                )
            )

        return plans

    def allocate(
        self,
        item: StageTypes.LinePlan,
        chosen: list[StageTypes.LineCandidate],
        method: StageTypes.LineMethod,
        probabilities: dict[str, float],
        drawn: dict[str, Decimal] | None = None,
    ) -> list[StageTypes.LineAllocation]:
        """
        Turn the PO lines chosen for one invoice line into allocations.

        drawn holds what earlier lines of this invoice already took from each PO line (PO-YYYY-NNNN/k).

        One PO line takes the whole quantity. Several fill the oldest PO first, each up to its quantity left, and the
        newest takes any rest; a portion of nothing is dropped. Several lines for a line with no quantity cannot be
        split, so the line is unmatched.
        """
        ordered = sorted(chosen, key=lambda candidate: (candidate.po_date, candidate.po_number, candidate.po_line))
        if len(ordered) > 1 and item.quantity is None:
            return [self._unmatched_line(item, probabilities)]

        if len(ordered) == 1 or item.quantity is None:
            return [self._allocation(item, ordered[0], item.quantity, method, probabilities)]

        portions: list[StageTypes.LineAllocation] = []
        rest = item.quantity
        for position, candidate in enumerate(ordered):
            left = candidate.left - (drawn or {}).get(candidate.key, Decimal(0))
            take = rest if position == len(ordered) - 1 else min(max(left, Decimal(0)), rest)
            if take > 0:
                portions.append(self._allocation(item, candidate, take, method, probabilities))
            rest -= take

        return portions or [self._allocation(item, ordered[0], item.quantity, method, probabilities)]

    def _settle(
        self,
        item: StageTypes.LinePlan,
        decisions: dict[int, tuple[list[str] | None, dict[str, float]]],
        drawn: dict[str, Decimal],
    ) -> list[StageTypes.LineAllocation]:
        """Return the allocations of one invoice line: the code's when sure, else Jev's, else unmatched."""
        if item.sure and item.suggestion is not None:
            return self.allocate(item, [item.suggestion], "code", {})

        chosen, probabilities = decisions.get(item.line, (None, {}))
        if chosen is None:
            return [self._unmatched_line(item, probabilities)]

        if not chosen:
            dropped = self._unmatched_line(item, probabilities)
            return [dropped.model_copy(update={"method": "jev_dropped"})]

        by_key = {candidate.key: candidate for candidate in item.ask}
        lines = [by_key[key] for key in chosen]
        # One invoice line is one item: it may draw on several PO lines only when they order that same item (near-
        # identical descriptions with the same numbers); several different items are held for a person.
        if not self._same_item(lines):
            return [self._unmatched_line(item, probabilities)]

        portions = self.allocate(item, lines, "jev_changed", probabilities, drawn)
        # Per portion: Jev kept the code's suggestion for this PO line, or chose another one.
        suggested = item.suggestion.key if item.suggestion else None
        return [
            portion.model_copy(update={"method": "jev_kept"})
            if portion.po_number is not None and f"{portion.po_number}/{portion.po_line}" == suggested
            else portion
            for portion in portions
        ]

    def _same_item(self, lines: list[StageTypes.LineCandidate]) -> bool:
        """Return whether every chosen PO line orders the same item as the others."""
        return all(
            self._order_entity.same_item(first.description, second.description)
            for index, first in enumerate(lines)
            for second in lines[index + 1 :]
        )

    @staticmethod
    def _allocation(
        item: StageTypes.LinePlan,
        candidate: StageTypes.LineCandidate,
        quantity: Decimal | None,
        method: StageTypes.LineMethod,
        probabilities: dict[str, float],
    ) -> StageTypes.LineAllocation:
        """Build one allocation of an invoice line (or one portion of it) to a PO line."""
        return StageTypes.LineAllocation(
            line=item.line,
            description=item.description,
            po_number=candidate.po_number,
            po_line=candidate.po_line,
            quantity=quantity,
            method=method,
            score=candidate.score,
            suggestion=item.suggestion.key if item.suggestion else None,
            probabilities=probabilities,
        )

    @staticmethod
    def _unmatched_line(item: StageTypes.LinePlan, probabilities: dict[str, float]) -> StageTypes.LineAllocation:
        """Build the allocation of an invoice line no PO line could be settled for."""
        return StageTypes.LineAllocation(
            line=item.line,
            description=item.description,
            po_number=None,
            po_line=None,
            quantity=item.quantity,
            method="unmatched",
            score=item.suggestion.score if item.suggestion else None,
            suggestion=item.suggestion.key if item.suggestion else None,
            probabilities=probabilities,
        )

    def _result(
        self,
        pool: list[StageTypes.OrderSnapshot],
        printed: list[str],
        allocations: list[StageTypes.LineAllocation],
        assist: StageTypes.AssistResult | None,
    ) -> StageTypes.OrderMatch:
        """Build the stage result: the POs the lines landed on, the allocations and the findings."""
        dates = {order.po_number: order.po_date for order in pool}
        used = sorted(
            {allocation.po_number for allocation in allocations if allocation.po_number is not None},
            key=lambda number: (dates[number], number),
        )
        open_lines = [allocation for allocation in allocations if allocation.po_number is None]
        by_jev = [allocation for allocation in allocations if allocation.method != "code"]
        findings: list[FindingTypes.Finding] = []
        if not open_lines and used and set(printed) <= set(used):
            findings.append(self._matched(printed, used))

        settled = [allocation for allocation in by_jev if allocation.po_number is not None]
        if settled:
            listed = "; ".join(
                f"line {allocation.line} → {allocation.po_number}/{allocation.po_line} "
                f"({allocation.probabilities.get(f'{allocation.po_number}/{allocation.po_line}', 0):.2f})"
                for allocation in settled
            )
            findings.append(
                FindingTypes.Finding.of(
                    "LINES_MATCHED_BY_JEV",
                    "info",
                    f"Jev settled the invoice lines the code was not sure about: {listed}.",
                    lines=listed,
                )
            )

        if open_lines:
            bands = (self._jev.reject_at_or_below, self._jev.select_above)
            listed = "; ".join(self._why(allocation, *bands) for allocation in open_lines)
            findings.append(
                FindingTypes.Finding.of(
                    "LINES_NOT_MATCHED",
                    "block",
                    f"Some invoice lines could not be matched to a PO line with confidence: {listed}. Match them by "
                    "hand.",
                    lines=listed,
                )
            )

        findings.extend(
            FindingTypes.Finding.of(
                "PO_UNUSED",
                "block",
                f"The invoice names {number}, but none of its lines is on it.",
                po_number=number,
            )
            for number in printed
            if number not in used
        )
        first = printed[0] if printed else (used[0] if used else None)
        others = [number for number in used if number != first]
        jev_used = any(allocation.method.startswith("jev") for allocation in allocations)
        method: StageTypes.OrderMethod = "explicit" if printed else ("jev" if jev_used else "lines")
        return StageTypes.OrderMatch(
            po_number=first,
            others=others,
            method=method if used or printed else "none",
            signals=[],
            score=100.0 if used and not open_lines else 0.0,
            needed=self._pass_score,
            candidates=[
                StageTypes.OrderCandidate(po_number=order.po_number, remaining=order.remaining, signals=[], score=0.0)
                for order in pool
            ],
            findings=findings,
            assist=assist,
            pool=[order.po_number for order in pool],
            allocations=allocations,
        )

    @staticmethod
    def _matched(printed: list[str], used: list[str]) -> FindingTypes.Finding:
        """Return the PO_MATCHED finding for an invoice whose every line was settled."""
        if printed and len(printed) == 1:
            message = f"Matched {printed[0]} by the reference printed on the invoice."
        elif printed:
            message = (
                f"Matched {' and '.join(printed)} by the references printed on the invoice; the invoice is split "
                "between them by its lines."
            )
        else:
            message = f"No PO number is printed; matched {' and '.join(used)} by the invoice lines."

        return FindingTypes.Finding.of(
            "PO_MATCHED", "info", message, po_number=used[0] if used else printed[0], others=",".join(used[1:])
        )

    @staticmethod
    def _why(allocation: StageTypes.LineAllocation, low: float, high: float) -> str:
        """Say why one invoice line was not matched."""
        what = f"line {allocation.line} ({allocation.description})"
        if allocation.method == "jev_dropped":
            return f"{what}: Jev found it on no PO line"

        chosen = [key for key, value in allocation.probabilities.items() if value > high]
        if len(chosen) > 1:
            return f"{what}: Jev chose PO lines that order different items ({', '.join(chosen)})"

        suggestion = (
            f"the code suggested {allocation.suggestion} (score {allocation.score:.0f})"
            if allocation.suggestion and allocation.score is not None
            else "no PO line resembles it"
        )
        unsure = [f"{key} ({value:.2f})" for key, value in allocation.probabilities.items() if low < value <= high]
        return f"{what}: {suggestion}" + (f"; Jev was unsure about {', '.join(unsure)}" if unsure else "")

    def _refused(
        self, refusals: list[FindingTypes.Finding], pool: list[StageTypes.OrderSnapshot]
    ) -> StageTypes.OrderMatch:
        """Build the result for an invoice whose printed PO numbers are refused or that has no pool."""
        return StageTypes.OrderMatch(
            po_number=None,
            method="none",
            signals=[],
            score=0.0,
            needed=self._pass_score,
            candidates=[],
            findings=refusals,
            pool=[order.po_number for order in pool],
        )

    @staticmethod
    def _refusal(
        po_number: str, order: StageTypes.OrderSnapshot | None, vendor_id: str | None
    ) -> FindingTypes.Finding | None:
        """Return why a printed PO number cannot be invoiced against, or None when it can."""
        if order is None:
            return FindingTypes.Finding.of(
                "PO_NOT_FOUND",
                "block",
                f"The invoice references {po_number}, which does not exist.",
                po_number=po_number,
                cause="refused",
            )

        if vendor_id is not None and order.vendor_id != vendor_id:
            return FindingTypes.Finding.of(
                "PO_VENDOR_MISMATCH",
                "block",
                f"The invoice references {po_number}, which belongs to {order.vendor_name} ({order.vendor_id}), not "
                f"to the invoicing vendor ({vendor_id}).",
                po_number=po_number,
                po_vendor=order.vendor_id,
                vendor_id=vendor_id,
            )

        if order.status == "cancelled":
            return FindingTypes.Finding.of(
                "PO_CANCELLED",
                "block",
                f"The invoice references {po_number}, which was cancelled; nothing may be invoiced against it.",
                po_number=po_number,
            )

        return None
