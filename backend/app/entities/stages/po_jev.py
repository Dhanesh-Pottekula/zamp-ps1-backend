"""
Stage 6, PO matching, part 2: Jev decides the invoice lines the code is not sure about.

- One request per invoice with the whole invoice as read (no bank details, no tax IDs), every pool PO in full (every
  line, what was ordered, already invoiced and left) and what the code concluded for each line
- One independent yes/no question per not-sure invoice line and pool PO line with something left: is this PO line a
  right line for that invoice line. Jev judges the item, then the price (among lines ordering that item, the ones at
  the invoice's unit price); lines that are equally right are all yes and the code fills the oldest PO first
- Per line: every PO line above select_above is chosen (one or several), all at or below reject_at_or_below means
  the line is on no PO line (Jev dropped it), anything in between means Jev is unsure and the line stays unmatched

Jev only says which PO lines. Quantities, prices, balances and the decision stay in code. A failed, slow or
unrecorded Jev answer leaves the not-sure lines unmatched, so the invoice is held.

Without a TypeSafe key the answers are replayed from a recordings file keyed by the SHA-256 of the request without
balances; with a key and record on, every live answer is written there.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Final, Literal

from app.entities._types import StageTypes

if TYPE_CHECKING:
    from decimal import Decimal
    from pathlib import Path

    from app.adapters import AdapterTypes
    from app.entities._types import ExtractionTypes


class _Helper:
    """Wording, request hashing and the recordings file."""

    LOGGER: Final = logging.getLogger("uvicorn.error")
    LINE_SITUATION: Final = (
        "An accounts-payable clerk is deciding which purchase-order lines each line of a supplier invoice bills. "
        "invoice is the whole invoice as read; purchase_orders lists every candidate PO in full with every line, its "
        "agreed unit price and the quantity left; code_conclusions says what the matching code proposed for each "
        "invoice line, with its description score out of 100, and whether it is sure. Every question is independent "
        "and asks whether one PO line is a right line for one invoice line. First the item: wording, abbreviations, "
        "synonyms, or a brand or model code that one side does not state are not differences; a size, model or "
        "service that contradicts the other side is. Then the price: when several PO lines order the item and some "
        "have the invoice line's unit price, the lines at another price are not right; when none or only one has "
        "that price, price rules nothing out. Lines that are equally right are all yes, even when one alone has "
        "enough left: the clerk fills the oldest PO first and checks the quantities and amounts."
    )
    BALANCES: Final = frozenset({"used", "remaining", "already_invoiced", "left", "status", "nothing_left"})

    @staticmethod
    def money(value: Decimal) -> str:
        """Format an amount with thousands separators and two decimals."""
        return f"{value:,.2f}"

    @staticmethod
    def plain(value: Decimal) -> str:
        """Format a quantity or rate without trailing zeros."""
        return f"{value.normalize():f}"

    @staticmethod
    def digest(key: dict[str, Any]) -> str:
        """Return the SHA-256 of a canonical recording key."""
        canonical = json.dumps(key, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def load(path: Path) -> dict[str, Any]:
        """Read the recordings file; a missing or empty file holds no recordings."""
        if not path.exists():
            return {}

        text = path.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else {}

    @staticmethod
    def save(path: Path, digest: str, entry: dict[str, Any]) -> None:
        """Add or replace one recording, sorted so diffs stay readable; written to a temp file, then swapped in."""
        recordings = _Helper.load(path)
        recordings[digest] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.tmp")
        partial.write_text(json.dumps(recordings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        partial.replace(path)


class PoJevClass:
    """Ask Jev which PO lines the invoice lines the code is not sure about bill."""

    def __init__(
        self,
        jev_adapter: AdapterTypes.Jev | None,
        *,
        recordings: Path,
        record: bool,
        select_above: float,
        reject_at_or_below: float,
        timeout: float,
    ) -> None:
        """Keep the Jev adapter (None in recorded mode), the recordings file and the confidence bands."""
        self._jev_adapter = jev_adapter
        self._recordings: Final = recordings
        self._record: Final = record and jev_adapter is not None
        self._select_above: Final = select_above
        self._reject_at_or_below: Final = reject_at_or_below
        self._timeout: Final = timeout
        self._write_lock = asyncio.Lock()

    @property
    def mode(self) -> Literal["live", "recorded"]:
        """Return live when a Jev adapter is attached; otherwise answers are replayed from recordings."""
        return "recorded" if self._jev_adapter is None else "live"

    async def ask(
        self, state: dict[str, Any], questions: dict[str, Any], key: dict[str, Any] | None = None
    ) -> tuple[dict[str, float] | None, str | None, str]:
        """
        Return Jev's probability of yes per question, or None with the reason it is unavailable, and the digest.

        Live mode asks Jev (and records the answer when recording is on); recorded mode replays a recording.
        """
        digest = _Helper.digest(key if key is not None else {"questions": questions, "state": state})
        if self._jev_adapter is None:
            try:
                recordings = await asyncio.to_thread(_Helper.load, self._recordings)
                entry = recordings.get(digest)
                answers = None if entry is None else {name: float(value) for name, value in entry["answers"].items()}
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                _Helper.LOGGER.warning("Jev recordings unreadable: %s", error)
                return None, f"the Jev recordings file could not be read: {type(error).__name__}", digest

            if answers is None:
                return None, "no recorded Jev answer matches this request; set TYPESAFE_API_KEY to ask Jev", digest

            return answers, None, digest

        # Any failure of the Jev call leaves the run as the code decided it; it must never fail the run.
        try:
            answers = await self._jev_adapter.evaluate(state, questions, timeout=self._timeout)
        except Exception as error:
            _Helper.LOGGER.warning("Jev request failed: %s", error)
            return None, f"Jev could not be reached: {type(error).__name__}", digest

        if self._record:
            entry = {"answers": answers, "source": "live", "pos": sorted(questions)}
            # A recording that cannot be written costs the next replay, never this run.
            try:
                async with self._write_lock:
                    await asyncio.to_thread(_Helper.save, self._recordings, digest, entry)
            except (OSError, ValueError, TypeError) as error:
                _Helper.LOGGER.warning("Jev answer not recorded: %s", error)

        return answers, None, digest

    def request_lines(
        self,
        reading: ExtractionTypes.InvoiceExtraction,
        *,
        vendor_name: str | None,
        taxable: Decimal | None,
        pool: list[StageTypes.OrderSnapshot],
        printed: list[str],
        plans: list[StageTypes.LinePlan],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """
        Build the state (whole invoice, every pool PO in full, the code's conclusions), the questions and the key.

        One yes/no question per not-sure invoice line and pool line with something left. The recording key is the
        state without balances and statuses, plus the questions, so a recording replays whatever ran before.
        """
        state: dict[str, Any] = {
            "situation": _Helper.LINE_SITUATION,
            "invoice": self._invoice(reading, vendor_name, taxable),
            "printed_po_numbers": printed,
            "purchase_orders": [self._full_order(order) for order in pool],
            "code_conclusions": [self._conclusion(plan) for plan in plans],
        }
        questions: dict[str, Any] = {}
        for plan in plans:
            if plan.sure:
                continue

            for candidate in plan.ask:
                questions[self.line_question(plan.line, candidate.key)] = {
                    "type": "noul",
                    "instructions": f"Is {candidate.po_number} line {candidate.po_line} ({candidate.description}) a "
                    f"right line for invoice line {plan.line} ({plan.description})? Judge the item, then the price.",
                    "criteria": {
                        "true": "The same product or service, even if worded differently, abbreviated, or described "
                        "with more or less detail; and it has the invoice line's unit price, or no other line ordering "
                        "this item has it.",
                        "false": "A different product or service (another size, model or service, or only a part or "
                        "accessory of it), or the same item at another price while another PO line ordering it has "
                        "the invoice line's unit price.",
                    },
                }

        key = {"state": self._without_balances(state), "questions": questions}
        return state, questions, key

    @staticmethod
    def line_question(line: int, po_line_key: str) -> str:
        """Return the question name for one invoice line and one PO line, using only letters, digits, - and _."""
        po_number, po_line = po_line_key.split("/")
        return f"line_{line}__{po_number}__{po_line}"

    @property
    def select_above(self) -> float:
        """Return the probability above which a PO line is chosen."""
        return self._select_above

    @property
    def reject_at_or_below(self) -> float:
        """Return the probability at or below which a PO line is rejected."""
        return self._reject_at_or_below

    async def decide(
        self,
        reading: ExtractionTypes.InvoiceExtraction,
        *,
        vendor_name: str | None,
        taxable: Decimal | None,
        pool: list[StageTypes.OrderSnapshot],
        printed: list[str],
        plans: list[StageTypes.LinePlan],
    ) -> tuple[dict[int, tuple[list[str] | None, dict[str, float]]], StageTypes.AssistResult]:
        """
        Ask Jev about every not-sure line; return per asked line its chosen PO lines and Jev's answers, and the record.

        Chosen is a list of PO lines (PO-YYYY-NNNN/k) above select_above, an empty list when every answer is at or
        below reject_at_or_below (Jev dropped the line), or None when any answer is in between or Jev did not answer.
        """
        state, questions, key = self.request_lines(
            reading, vendor_name=vendor_name, taxable=taxable, pool=pool, printed=printed, plans=plans
        )
        asked = [plan.line for plan in plans if not plan.sure and plan.ask]
        if not questions:
            return {}, StageTypes.AssistResult(
                state="skipped", reason="no line needed Jev", mode=self.mode, digest=None, asked=[], findings=[]
            )

        probabilities, reason, digest = await self.ask(state, questions, key)
        if probabilities is not None and set(probabilities) != set(questions):
            reason = f"Jev answered {len(probabilities)} questions but was asked {len(questions)}"
            probabilities = None
        if probabilities is not None and any(not 0 <= value <= 1 for value in probabilities.values()):
            reason, probabilities = "Jev returned a value outside 0 to 1", None

        if probabilities is None:
            record = StageTypes.AssistResult(
                state="skipped",
                reason=reason or "Jev did not answer",
                mode=self.mode,
                digest=digest,
                asked=asked,
                findings=[],
            )
            return {line: (None, {}) for line in asked}, record

        decisions: dict[int, tuple[list[str] | None, dict[str, float]]] = {}
        for plan in plans:
            if plan.sure or not plan.ask:
                continue

            answers = {
                candidate.key: probabilities[self.line_question(plan.line, candidate.key)] for candidate in plan.ask
            }
            unsure = any(self._reject_at_or_below < value <= self._select_above for value in answers.values())
            chosen = None if unsure else [name for name, value in answers.items() if value > self._select_above]
            decisions[plan.line] = (chosen, answers)

        record = StageTypes.AssistResult(
            state="answered", reason=None, mode=self.mode, digest=digest, asked=asked, findings=[]
        )
        return decisions, record

    @staticmethod
    def _invoice(
        reading: ExtractionTypes.InvoiceExtraction, vendor_name: str | None, taxable: Decimal | None
    ) -> dict[str, Any]:
        """Describe the whole invoice as read; bank details and tax IDs are left out (who is paid, not what)."""

        def text(value: Decimal | None) -> str | None:
            return None if value is None else f"{value.normalize():f}"

        return {
            "supplier": vendor_name or reading.supplier.name,
            "invoice_number": reading.invoice_number,
            "invoice_date": reading.invoice_date.isoformat() if reading.invoice_date else None,
            "due_date": reading.due_date.isoformat() if reading.due_date else None,
            "currency": reading.currency,
            "po_reference": reading.po_reference_text,
            "lines": [
                {
                    "line": index,
                    "description": line.description,
                    "quantity": text(line.quantity),
                    "unit": line.unit,
                    "unit_price": text(line.unit_price),
                    "line_amount": text(line.line_amount),
                    "tax_rate_pct": text(line.tax_rate_pct),
                }
                for index, line in enumerate(reading.lines, start=1)
            ],
            "subtotal": text(reading.subtotal),
            "tax_lines": [
                {"label": tax.label, "rate_pct": text(tax.rate_pct), "amount": text(tax.amount)}
                for tax in reading.tax_lines
            ],
            "total": text(reading.total),
            "taxable_value": text(taxable),
        }

    @staticmethod
    def _full_order(order: StageTypes.OrderSnapshot) -> dict[str, Any]:
        """Describe one pool PO in full, every line included; a line with nothing left is marked."""
        return {
            "po_number": order.po_number,
            "po_date": order.po_date.isoformat(),
            "currency": order.currency,
            "status": order.status,
            "total": _Helper.money(order.total),
            "used": _Helper.money(order.consumed),
            "remaining": _Helper.money(order.remaining),
            "lines": [
                {
                    "line": line.line_no,
                    "description": line.description,
                    "ordered": _Helper.plain(line.quantity),
                    "already_invoiced": _Helper.plain(line.consumed_qty),
                    "left": _Helper.plain(line.quantity - line.consumed_qty),
                    "unit_price": _Helper.money(line.unit_price),
                    "tax_rate_pct": _Helper.plain(line.tax_rate_pct),
                    "nothing_left": line.quantity - line.consumed_qty <= 0,
                }
                for line in order.lines
            ],
        }

    @staticmethod
    def _conclusion(plan: StageTypes.LinePlan) -> dict[str, Any]:
        """Describe what the code concluded for one invoice line."""
        return {
            "line": plan.line,
            "sure": plan.sure,
            "suggestion": plan.suggestion.key if plan.suggestion else None,
            "suggestion_score": round(plan.suggestion.score) if plan.suggestion else None,
            "suggestion_coverage": round(plan.suggestion.coverage) if plan.suggestion else None,
            "similar_lines": [{"po_line": rival.key, "score": round(rival.score)} for rival in plan.rivals],
        }

    @staticmethod
    def _without_balances(value: Any) -> Any:
        """Return a copy without the balance and status fields, which change as earlier invoices are approved."""
        if isinstance(value, dict):
            return {
                name: PoJevClass._without_balances(item) for name, item in value.items() if name not in _Helper.BALANCES
            }

        if isinstance(value, list):
            return [PoJevClass._without_balances(item) for item in value]

        return value
