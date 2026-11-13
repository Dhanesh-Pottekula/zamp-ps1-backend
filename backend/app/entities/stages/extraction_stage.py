"""
Stage 1, extraction: one model call per document, a recorded reading found by the document's content, or a typed
manual entry, then conversion of the text into typed values.
"""

import asyncio
import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Final

from app.adapters import ExtractionError
from app.entities._types import ExtractionTypes, FindingTypes, StageTypes

if TYPE_CHECKING:
    from app.adapters import AdapterTypes
    from app.entities.prompt_ent import PromptEntityClass
    from app.entities.seed_ent import SeedEntityClass


class _Helper:
    """Parse printed amounts and dates and build the normalised invoice."""

    NUMBER: Final = re.compile(r"\d[\d.,]*")
    DIGIT_GROUPING: Final = re.compile("(?<=\\d)['\u2019](?=\\d)")
    ISO_DATE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    NUMERIC_DATE: Final = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})(?!\d)")
    TEXT_DATE_FORMATS: Final = (
        "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d. %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y",
    )  # fmt: skip

    @staticmethod
    def text(value: str) -> str | None:
        """Return the stripped text, or None when it is blank."""
        return value.strip() or None

    @staticmethod
    def amount(value: str, *, decimal_comma: bool = False, dots_unknown: bool = False) -> Decimal | None:
        """
        Parse a printed amount: drop symbols, letters, spaces and digit-grouping marks.

        With decimal_comma the document writes 1.234,56 or 1 234,56: dots and spaces group and the one comma is the
        decimal point. Otherwise commas group (1,234.56 and 1,70,000.00) and a European-looking form such as
        36.000,00 returns None rather than a wrong number. dots_unknown is set when the document showed no decimal
        character at all: then a whole number grouped by dots (36.000, 1.250) could be thirty-six thousand or 36.0,
        so it returns None too. A minus sign outside the digits, or parentheses around them, means negative.
        """
        compact = _Helper.DIGIT_GROUPING.sub("", re.sub(r"\s", "", value))
        match = _Helper.NUMBER.search(compact)
        if match is None:
            return None

        token = match.group().rstrip(".,")
        if decimal_comma:
            if token.count(",") > 1 or re.search(r",\d{3}\.", token):
                return None

            token = token.replace(".", "").replace(",", ".")
        elif dots_unknown and re.fullmatch(r"\d{1,3}(\.\d{3})+", token):
            return None
        else:
            comma_last = "," in token and token.rfind(",") > token.rfind(".")
            if token.count(".") > 1 or (comma_last and ("." in token or re.search(r",\d{1,2}$", token))):
                return None

            token = token.replace(",", "")

        try:
            number = Decimal(token)
        except InvalidOperation:
            return None

        before, after = compact[: match.start()], compact[match.end() :]
        negative = "-" in before or "-" in after or ("(" in before and ")" in after)
        return -number if negative else number

    @staticmethod
    def day(value: str, order: ExtractionTypes.DateOrder) -> date | None:
        """Parse YYYY-MM-DD, a numeric date in the document's order (day first when unknown), or a month name."""
        text = value.strip()
        if _Helper.ISO_DATE.match(text):
            try:
                return date.fromisoformat(text)
            except ValueError:
                return None

        numeric = _Helper.NUMERIC_DATE.fullmatch(text)
        if numeric is not None:
            return _Helper.numeric_day(numeric, order)

        for layout in _Helper.TEXT_DATE_FORMATS:
            try:
                return datetime.strptime(text, layout).date()
            except ValueError:
                continue

        return None

    @staticmethod
    def numeric_day(match: re.Match[str], order: ExtractionTypes.DateOrder) -> date | None:
        """Build a date from a matched dd/mm/yyyy-style triple, month first only when the order is MDY."""
        first, second, year = (int(part) for part in match.groups())
        day, month = (second, first) if order == "MDY" else (first, second)
        try:
            return date(year + 2000 if year < 100 else year, month, day)
        except ValueError:
            return None

    @staticmethod
    def dated(
        field: str, value: str, quotes: dict[str, str], order: ExtractionTypes.DateOrder
    ) -> tuple[date | None, bool]:
        """
        Return one date and whether its printed form is ambiguous.

        The model's value is the reading. When the field's source quote prints the same date numerically (as either
        day/month or month/day), code settles the order itself: the document's order when the model reported one,
        else whichever part is above 12, else day first and flagged ambiguous. A quote that cannot produce the
        model's value is about something else and changes nothing; an empty value stays empty.
        """
        reading = _Helper.day(value, order)
        printed = _Helper.NUMERIC_DATE.search(quotes.get(field, ""))
        if reading is None or printed is None:
            return reading, False

        if reading not in {_Helper.numeric_day(printed, "DMY"), _Helper.numeric_day(printed, "MDY")}:
            return reading, False

        # A part above 12 can only be the day, whatever order was reported; only a truly ambiguous date uses the order.
        first, second = int(printed.group(1)), int(printed.group(2))
        if first > 12 or second > 12:
            return _Helper.numeric_day(printed, "MDY" if second > 12 else "DMY"), False

        if order in {"DMY", "MDY"}:
            return _Helper.numeric_day(printed, order), False

        return _Helper.numeric_day(printed, "DMY"), first != second

    @staticmethod
    def convert(read: ExtractionTypes.InvoiceRead) -> ExtractionTypes.InvoiceExtraction:
        """Turn the wire reading into the normalised extraction, parsing numbers and dates in the document's style."""
        text = _Helper.text
        decimal_comma, dots_unknown = read.decimal_separator == ",", read.decimal_separator == ""

        def amount(value: str) -> Decimal | None:
            return _Helper.amount(value, decimal_comma=decimal_comma, dots_unknown=dots_unknown)

        quotes = {source.field: source.text for source in read.sources if source.text.strip()}
        invoice_date, invoice_unclear = _Helper.dated("invoice_date", read.invoice_date, quotes, read.date_order)
        due_date, due_unclear = _Helper.dated("due_date", read.due_date, quotes, read.date_order)
        unclear = [name for name, flag in (("invoice_date", invoice_unclear), ("due_date", due_unclear)) if flag]
        return ExtractionTypes.InvoiceExtraction(
            supplier=ExtractionTypes.Party(
                name=text(read.supplier_name),
                tax_id=text(read.supplier_tax_id),
                address=text(read.supplier_address),
                email=text(read.supplier_email),
            ),
            supplier_bank=ExtractionTypes.BankDetails(
                account_name=text(read.bank_account_name),
                account_number=text(read.bank_account_number),
                bank_code=text(read.bank_code),
                bank_name=text(read.bank_name),
            ),
            buyer=ExtractionTypes.Party(
                name=text(read.buyer_name), tax_id=text(read.buyer_tax_id), address=None, email=None
            ),
            invoice_number=text(read.invoice_number),
            invoice_date=invoice_date,
            due_date=due_date,
            ambiguous_dates=unclear,
            currency=(text(read.currency) or "").upper() or None,
            po_reference_text=text(read.po_reference_text),
            prices_include_tax=read.prices_include_tax,
            lines=[
                ExtractionTypes.LineItem(
                    description=text(line.description),
                    quantity=amount(line.quantity),
                    unit=text(line.unit),
                    unit_price=amount(line.unit_price),
                    line_amount=amount(line.line_amount),
                    tax_rate_pct=amount(line.tax_rate_pct),
                )
                for line in read.lines
            ],
            subtotal=amount(read.subtotal),
            tax_lines=[
                ExtractionTypes.TaxLine(label=text(tax.label), rate_pct=amount(tax.rate_pct), amount=amount(tax.amount))
                for tax in read.tax_lines
            ],
            total=amount(read.total),
            sources=[
                ExtractionTypes.SourceQuote(field=source.field, text=source.text)
                for source in read.sources
                if source.text.strip()
            ],
        )


class ExtractionStageClass:
    """Read a document with the model, or in recorded mode with the sample recording of identical bytes."""

    def __init__(
        self,
        llm_adapter: AdapterTypes.LLM | None,
        prompt_entity: PromptEntityClass,
        model: str,
        *,
        seed_entity: SeedEntityClass,
        replay_delay: float,
    ) -> None:
        """Keep the model adapter (None in recorded mode), the prompts, the recordings and the replay pause."""
        self._llm_adapter = llm_adapter
        self._prompt_entity = prompt_entity
        self._seed_entity = seed_entity

        self.mode: Final[StageTypes.LlmMode] = "recorded" if llm_adapter is None else "live"
        self.model: Final = model
        self._replay_delay: Final = replay_delay
        self._unmatched: Final = (
            "no recorded reading matches this document; set ANTHROPIC_API_KEY or OPENAI_API_KEY to read new "
            "documents, or enter the invoice by hand"
        )

    async def extract(
        self, document: bytes, media_type: AdapterTypes.MediaType, filename: str | None
    ) -> StageTypes.ExtractionResult:
        """Return the normalised extraction, or EXTRACTION_FAILED (block) when the document could not be read."""
        started = time.perf_counter()
        if self._llm_adapter is None:
            reading = await self._seed_entity.recording(document)
            if reading is None:
                return self._failed(self._unmatched, f"The invoice could not be read: {self._unmatched}.", started)

            await asyncio.sleep(self._replay_delay)
            return self._read(reading, StageTypes.Usage(input=0, cache_read=0, cache_write=0, output=0), started)

        try:
            reading, usage = await self._llm_adapter.extract(
                document,
                media_type=media_type,
                system_prompt=self._prompt_entity.system,
                context_prompt=self._prompt_entity.context(filename),
                response_model=ExtractionTypes.InvoiceRead,
            )
        except ExtractionError as error:
            message = f"The invoice could not be read: {error}. Retry the run or enter the invoice manually."
            return self._failed(str(error), message, started)

        return self._read(reading, usage, started)

    @staticmethod
    def typed(reading: ExtractionTypes.InvoiceRead) -> StageTypes.ExtractionResult:
        """Convert an invoice a person typed, exactly as a model reading is converted; no model is called."""
        return StageTypes.ExtractionResult(
            extraction=_Helper.convert(reading), usage=None, mode="manual", model="none", seconds=0.0, findings=[]
        )

    def _read(
        self, reading: ExtractionTypes.InvoiceRead, usage: StageTypes.Usage, started: float
    ) -> StageTypes.ExtractionResult:
        """Return the converted reading with its usage."""
        return StageTypes.ExtractionResult(
            extraction=_Helper.convert(reading),
            usage=usage,
            mode=self.mode,
            model=self.model,
            seconds=round(time.perf_counter() - started, 3),
            findings=[],
        )

    def _failed(self, error: str, message: str, started: float) -> StageTypes.ExtractionResult:
        """Return no reading and the blocking EXTRACTION_FAILED finding."""
        finding = FindingTypes.Finding(
            code="EXTRACTION_FAILED", severity="block", message=message, data={"error": error}
        )
        return StageTypes.ExtractionResult(
            extraction=None,
            usage=None,
            mode=self.mode,
            model=self.model,
            seconds=round(time.perf_counter() - started, 3),
            findings=[finding],
        )
