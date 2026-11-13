"""Stage results, run states and the event the pipeline publishes for every step."""

from datetime import date  # noqa: TC003 - pydantic resolves field types at runtime
from decimal import Decimal  # noqa: TC003 - pydantic resolves field types at runtime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.adapters import AdapterTypes
from app.entities._types.extraction_types import ExtractionTypes  # noqa: TC001 - pydantic resolves field types at runtime
from app.entities._types.finding_types import FindingTypes  # noqa: TC001 - pydantic resolves field types at runtime


class _Types:
    """Every stage result plus the shared PO and line shapes the stages exchange."""

    type StepName = Literal[
        "intake", "extraction", "validation", "vendor", "bank", "duplicate", "po", "amounts", "decision", "persist"
    ]
    type StepState = Literal["running", "done", "skipped", "failed"]
    type RunStatus = Literal["running", "completed", "error"]
    type Outcome = Literal["approved", "needs_attention", "rejected"]
    type LlmMode = Literal["live", "recorded"]
    type RunMode = Literal["live", "recorded", "manual"]
    type OrderStatus = Literal["open", "partially_invoiced", "fully_invoiced", "cancelled"]
    type VendorMethod = Literal["tax_id", "bank_account", "name_fuzzy", "none"]
    type OrderMethod = Literal["explicit", "inferred", "lines", "jev", "none"]
    type LineMethod = Literal["code", "jev_kept", "jev_changed", "jev_dropped", "unmatched"]
    type SignalName = Literal["tax_id", "bank_account", "name"]
    type SignalState = Literal["matched", "mismatched", "absent"]
    type GateName = Literal["vendor", "bank", "po", "amount"]

    class IntakeResult(BaseModel):
        """What was received and whether it can be processed; filename is None when the upload had no usable name."""

        filename: str | None
        media_type: str
        size_bytes: int
        page_count: int | None
        encrypted: bool
        text_layer: bool | None
        accepted: bool
        findings: list[FindingTypes.Finding]

    class ExtractionResult(BaseModel):
        """The normalised reading, or None when the model call failed."""

        extraction: ExtractionTypes.InvoiceExtraction | None
        usage: AdapterTypes.Usage | None
        mode: _Types.RunMode
        model: str
        seconds: float
        findings: list[FindingTypes.Finding]

    class ValidationResult(BaseModel):
        """The taxable value later stages compare, and the arithmetic checks on the reading."""

        taxable_value: Decimal | None
        tax_total: Decimal | None
        tax_rate_pct: Decimal | None
        derived: bool
        findings: list[FindingTypes.Finding]

    class VendorCandidate(BaseModel):
        """A master vendor considered; score is its name similarity (basis name) or its weighted score (weights)."""

        vendor_id: str
        legal_name: str
        score: float
        basis: Literal["name", "weights"]

    class VendorSignal(BaseModel):
        """One identity field compared with a master vendor; absent means the invoice or the master has no value."""

        signal: _Types.SignalName
        state: _Types.SignalState
        weight: float
        points: float

    class VendorMatch(BaseModel):
        """
        Who issued the invoice, and how that was established.

        score is the sum of the matched signals' weights, capped at 100; the vendor is identified only above needed.
        """

        vendor_id: str | None
        legal_name: str | None
        method: _Types.VendorMethod
        score: float
        needed: float
        evidence: list[_Types.VendorSignal]
        candidates: list[_Types.VendorCandidate]
        findings: list[FindingTypes.Finding]

    class BankCheck(BaseModel):
        """
        The bank account the invoice asks to be paid to, compared with the identified vendor's master account.

        Accounts are masked to their last four digits; absent means the invoice prints no account.
        """

        vendor_id: str
        state: Literal["matched", "mismatched", "absent"]
        invoice_account: str | None
        master_account: str
        findings: list[FindingTypes.Finding]

    class AssistCandidate(BaseModel):
        """An open PO offered to Jev, with Jev's probability that the invoice bills it and its band."""

        po_number: str
        po_date: date
        remaining: Decimal
        probability: float | None
        verdict: Literal["selected", "unsure", "rejected"] | None

    class AssistResult(BaseModel):
        """
        Jev's part of the PO stage: what it was asked and how it answered.

        asked lists the invoice lines (1-based) Jev decided. The PO-selection fields (candidates, selected, confident,
        code_method, code_findings) belong to runs stored before line matching and stay readable.
        """

        state: Literal["answered", "skipped"]
        reason: str | None
        mode: Literal["live", "recorded"]
        digest: str | None
        asked: list[int] = []
        candidates: list[_Types.AssistCandidate] = []
        selected: list[str] = []
        confident: bool = False
        code_method: _Types.OrderMethod | None = None
        code_findings: list[FindingTypes.Finding] = []
        findings: list[FindingTypes.Finding]

    class LineCandidate(BaseModel):
        """One PO line of the pool scored against one invoice line by description (0 to 100)."""

        po_number: str
        po_line: int
        po_date: date
        description: str
        score: float
        coverage: float = 100.0
        left: Decimal

        @property
        def key(self) -> str:
            """Return the PO line as PO-YYYY-NNNN/k."""
            return f"{self.po_number}/{self.po_line}"

    class LinePlan(BaseModel):
        """
        The code's view of one invoice line before Jev: whether it is sure, its suggestion and the lines to ask about.

        rivals are the other pool lines scoring at or above the candidate cut-off (they make a match unsure); ask are
        the pool lines with something left that Jev is asked about when the line is not sure.
        """

        line: int
        description: str
        quantity: Decimal | None
        sure: bool
        suggestion: _Types.LineCandidate | None
        rivals: list[_Types.LineCandidate]
        ask: list[_Types.LineCandidate]

    class LineAllocation(BaseModel):
        """
        Where one invoice line (or one portion of it) is billed: a PO line and a quantity, and how that was settled.

        A line Jev put on several PO lines has one allocation per portion. Unmatched and dropped lines have no PO.
        probabilities are Jev's answers for this invoice line, keyed PO-YYYY-NNNN/k.
        """

        line: int
        description: str
        po_number: str | None
        po_line: int | None
        quantity: Decimal | None
        method: _Types.LineMethod
        score: float | None
        suggestion: str | None
        probabilities: dict[str, float] = {}

    class OrderCandidate(BaseModel):
        """An open PO considered by inference, with the signals it showed."""

        po_number: str
        remaining: Decimal
        signals: list[str]
        score: float

    class OrderMatch(BaseModel):
        """
        Which PO the invoice draws on, and how that was established.

        score is the sum of the signals' weights, capped at 100; the PO may be paid against only above needed.
        others holds the further POs an invoice prints after the first (or Jev selected after the first); the invoice
        is then split between them all. assist is Jev's selection when the code could not choose the PO, else None.
        """

        po_number: str | None
        others: list[str] = []
        method: _Types.OrderMethod
        signals: list[str]
        score: float
        needed: float
        candidates: list[_Types.OrderCandidate]
        findings: list[FindingTypes.Finding]
        assist: _Types.AssistResult | None = None
        pool: list[str] = []
        allocations: list[_Types.LineAllocation] = []

    class LineResult(BaseModel):
        """One invoice line checked against its PO line."""

        description: str
        po_number: str | None = None
        po_line: int | None
        quantity: Decimal | None
        ordered: Decimal | None
        consumed_before: Decimal | None
        unit_price: Decimal | None
        po_price: Decimal | None
        ok: bool

    class AmountPart(BaseModel):
        """The share of a split invoice that falls on one PO, compared with that PO's balance."""

        po_number: str
        invoice_amount: Decimal
        remaining_before: Decimal
        variance: Decimal
        tolerance: Decimal
        within: bool

    class AmountCheck(BaseModel):
        """
        The taxable value compared with the PO balance, and the per-line checks.

        For an invoice split between several POs the amounts are totals over those POs and parts holds each share.
        """

        basis: Literal["taxable_value"]
        invoice_amount: Decimal
        remaining_before: Decimal
        variance: Decimal
        tolerance: Decimal
        within: bool
        level: Literal["line", "header"]
        lines: list[_Types.LineResult]
        parts: list[_Types.AmountPart] = []
        findings: list[FindingTypes.Finding]

    class DuplicateCheck(BaseModel):
        """Whether the invoice was already registered."""

        verdict: Literal["none", "exact", "suspected"]
        prior_run: str | None
        findings: list[FindingTypes.Finding]

    class OrderUpdate(BaseModel):
        """What an approval consumes from the PO; quantities maps PO line number to quantity."""

        po_number: str
        amount: Decimal
        remaining_after: Decimal
        quantities: dict[int, Decimal]

    class Gate(BaseModel):
        """One of the three checks an approval needs; it passes when its stage ran and raised no block finding."""

        name: _Types.GateName
        passed: bool
        detail: str

    class Decision(BaseModel):
        """
        The PO update and the four gates; others holds the updates of the further POs of a split invoice.

        In memory only: the outcome, its one-sentence summary and every finding in reason order.
        """

        outcome: _Types.Outcome = Field(exclude=True)
        summary: str = Field(exclude=True)
        reasons: list[FindingTypes.Finding] = Field(exclude=True)
        gates: list[_Types.Gate]
        update: _Types.OrderUpdate | None
        others: list[_Types.OrderUpdate] = []

    class StepEvent(BaseModel):
        """One step transition, published live and stored when final."""

        run_id: str
        name: _Types.StepName
        state: _Types.StepState
        millis: int
        data: dict[str, Any]
        findings: list[FindingTypes.Finding]

    class RunClosed(BaseModel):
        """The final event of a run."""

        status: _Types.RunStatus
        outcome: _Types.Outcome | None
        summary: str | None

    class RunClosing(BaseModel):
        """Everything written on a run when it ends."""

        status: _Types.RunStatus
        outcome: _Types.Outcome | None
        summary: str
        vendor_id: str | None
        po_number: str | None
        millis: int

    class PricedLine(BaseModel):
        """An invoice line with its unit price brought to the pre-tax basis POs are stored in."""

        description: str
        quantity: Decimal | None
        unit_price: Decimal | None
        tax_rate_pct: Decimal | None

    class OrderLine(BaseModel):
        """One PO line with what has been consumed from it."""

        line_no: int
        description: str
        quantity: Decimal
        unit: str
        unit_price: Decimal
        tax_rate_pct: Decimal
        consumed_qty: Decimal

    class OrderSnapshot(BaseModel):
        """A PO header with its balance and lines."""

        po_number: str
        vendor_id: str
        vendor_name: str
        po_date: date
        currency: str
        status: _Types.OrderStatus
        total: Decimal
        consumed: Decimal
        remaining: Decimal
        lines: list[_Types.OrderLine]


class StageTypes:
    """Public stage types."""

    type StepName = _Types.StepName
    type StepState = _Types.StepState
    type RunStatus = _Types.RunStatus
    type Outcome = _Types.Outcome
    type LlmMode = _Types.LlmMode
    type RunMode = _Types.RunMode
    type OrderStatus = _Types.OrderStatus
    type VendorMethod = _Types.VendorMethod
    type SignalName = _Types.SignalName
    type SignalState = _Types.SignalState
    type GateName = _Types.GateName
    Usage = AdapterTypes.Usage
    IntakeResult = _Types.IntakeResult
    ExtractionResult = _Types.ExtractionResult
    ValidationResult = _Types.ValidationResult
    VendorCandidate = _Types.VendorCandidate
    VendorSignal = _Types.VendorSignal
    VendorMatch = _Types.VendorMatch
    BankCheck = _Types.BankCheck
    OrderCandidate = _Types.OrderCandidate
    OrderMatch = _Types.OrderMatch
    LineResult = _Types.LineResult
    AmountPart = _Types.AmountPart
    AmountCheck = _Types.AmountCheck
    DuplicateCheck = _Types.DuplicateCheck
    OrderUpdate = _Types.OrderUpdate
    Gate = _Types.Gate
    Decision = _Types.Decision
    StepEvent = _Types.StepEvent
    RunClosed = _Types.RunClosed
    RunClosing = _Types.RunClosing
    PricedLine = _Types.PricedLine
    OrderLine = _Types.OrderLine
    OrderSnapshot = _Types.OrderSnapshot
    AssistCandidate = _Types.AssistCandidate
    AssistResult = _Types.AssistResult
    LineCandidate = _Types.LineCandidate
    LinePlan = _Types.LinePlan
    LineAllocation = _Types.LineAllocation
    type LineMethod = _Types.LineMethod
