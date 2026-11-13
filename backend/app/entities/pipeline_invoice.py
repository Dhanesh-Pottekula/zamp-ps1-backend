"""
The invoice pipeline: the only place that knows the stage order. Each stage's logic lives in its own module under
stages/; this file runs them in order, records each step and handles locking and failures.

0 intake, 1 extraction, 2 validation, 3 vendor, 4 bank, 5 duplicate, 6 po, 7 amounts, 8 decision, 9 persist.
Inside stage 6, when the invoice prints no PO number and the code cannot choose among the vendor's open POs, Jev
selects the POs; stage 7 then checks the invoice against whichever POs stage 6 settled on.
A manual run starts from a typed reading instead of a file: intake is skipped and extraction only converts it.
A block finding never stops later stages (report-only); a stage is skipped only when its input does not exist. An
unexpected exception marks the step failed and the run error, never approved.

Runs proceed in parallel. Stages 0 to 4 touch only the run's own document and the vendor master, so nothing waits
for them, including the model call. Stages 5 to 9 read and write data shared with other runs of the same vendor
(that vendor's POs and approved invoices), so they hold one lock per vendor: two invoices from one vendor take turns,
and the second sees the first's approval and PO usage. A run with no matched vendor is blocked from approval, so it
consumes no PO and never counts in a duplicate check, and it needs no lock. A reset does not wait for runs; a run it
deleted stores no further step and its persist stage writes nothing (see RunEntityClass.record and close_in).
"""

import asyncio
import contextlib
import inspect
import logging
import re
import time
from datetime import date
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel

from app.adapters import AdapterTypes  # noqa: TC001 - pydantic resolves field types at runtime
from app.entities._types import ExtractionTypes, FindingTypes, StageTypes

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from app.entities.rules_ent import RulesEntityClass
    from app.entities.run_ent import RunEntityClass
    from app.entities.seed_ent import SeedEntityClass
    from app.entities.stages.amounts_stage import AmountsStageClass
    from app.entities.stages.bank_stage import BankStageClass
    from app.entities.stages.decision_stage import DecisionStageClass
    from app.entities.stages.duplicate_stage import DuplicateStageClass
    from app.entities.stages.extraction_stage import ExtractionStageClass
    from app.entities.stages.intake_stage import IntakeStageClass
    from app.entities.stages.persist_stage import PersistStageClass
    from app.entities.stages.po_stage import PoStageClass
    from app.entities.stages.validation_stage import ValidationStageClass
    from app.entities.stages.vendor_stage import VendorStageClass
    from app.entities.vendor_ent import VendorEntityClass


class _Types:
    """What one run has produced so far."""

    class Trace(BaseModel):
        """Stage results of one run, filled as the stages complete."""

        stage: StageTypes.StepName = "intake"
        media_type: AdapterTypes.MediaType | None = None
        intake: StageTypes.IntakeResult | None = None
        extraction: StageTypes.ExtractionResult | None = None
        validation: StageTypes.ValidationResult | None = None
        vendor: StageTypes.VendorMatch | None = None
        bank: StageTypes.BankCheck | None = None
        duplicate: StageTypes.DuplicateCheck | None = None
        order: StageTypes.OrderMatch | None = None
        amounts: StageTypes.AmountCheck | None = None

        def findings(self) -> list[FindingTypes.Finding]:
            """Return every finding so far, in stage order."""
            results = (
                self.intake,
                self.extraction,
                self.validation,
                self.vendor,
                self.bank,
                self.duplicate,
                self.order,
                self.amounts,
            )
            return [finding for result in results if result is not None for finding in result.findings]


class _Helper:
    """Small pure helpers for the pipeline."""

    LOGGER: Final = logging.getLogger("uvicorn.error")

    @staticmethod
    def safe_name(filename: str | None) -> str | None:
        """Keep only the last path segment of an uploaded name; a missing, empty, '.' or '..' name has none."""
        name = re.split(r"[\\/]", filename or "")[-1].strip()
        return None if name in {"", ".", ".."} else name

    @staticmethod
    def millis(started: float) -> int:
        """Return whole milliseconds since started."""
        return round((time.perf_counter() - started) * 1000)


class PipelineEntityClass:
    """Run documents through the ten stages in order; stages 5 to 9 hold the matched vendor's lock."""

    def __init__(
        self,
        store_adapter: AdapterTypes.Store,
        *,
        intake: IntakeStageClass,
        extraction: ExtractionStageClass,
        validation: ValidationStageClass,
        vendor: VendorStageClass,
        bank: BankStageClass,
        duplicate: DuplicateStageClass,
        po: PoStageClass,
        amounts: AmountsStageClass,
        decision: DecisionStageClass,
        persist: PersistStageClass,
        rules_entity: RulesEntityClass,
        vendor_entity: VendorEntityClass,
        run_entity: RunEntityClass,
        seed_entity: SeedEntityClass,
    ) -> None:
        """Keep the ten stages, the policy and data they share, the vendor locks and the set of running tasks."""
        self._store_adapter = store_adapter
        self._intake = intake
        self._extraction = extraction
        self._validation = validation
        self._vendor = vendor
        self._bank = bank
        self._duplicate = duplicate
        self._po = po
        self._amounts = amounts
        self._decision = decision
        self._persist = persist
        self._rules_entity = rules_entity
        self._vendor_entity = vendor_entity
        self._run_entity = run_entity
        self._seed_entity = seed_entity
        self._vendor_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()

        self.max_bytes: Final = intake.max_bytes

    async def submit(self, filename: str | None, data: bytes, sample: str | None) -> str:
        """Create the run, schedule its processing in the background and return its id at once."""
        name = _Helper.safe_name(filename)
        run_id = await self._run_entity.start(name, sample, self._extraction.mode)
        self._schedule(run_id, name, data)
        return run_id

    async def enter(self, reading: ExtractionTypes.InvoiceRead) -> str:
        """Create a manual run from a typed invoice, schedule its processing in the background and return its id."""
        run_id = await self._run_entity.start(None, None, "manual")
        self._schedule(run_id, None, reading)
        return run_id

    def _schedule(self, run_id: str, filename: str | None, source: bytes | ExtractionTypes.InvoiceRead) -> None:
        """Start processing a run as a background task that is cancelled on shutdown."""
        task = asyncio.create_task(self._process(run_id, filename, source))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def document(self, run_id: str) -> tuple[Path, AdapterTypes.MediaType] | None:
        """Return the stored document of a run with its media type; None for a typed or rejected invoice."""
        return await self._store_adapter.find(run_id)

    async def reset(self) -> None:
        """Restore the seed data at once; runs in progress are cut off and store nothing more."""
        await self._seed_entity.reset()

    async def close(self) -> None:
        """Cancel every scheduled run and wait for the cancellations to settle."""
        for task in self._tasks:
            task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _process(self, run_id: str, filename: str | None, source: bytes | ExtractionTypes.InvoiceRead) -> None:
        """Run every stage of one document; stages 5 to 9 hold the matched vendor's lock."""
        started = time.perf_counter()
        trace = _Types.Trace()

        # A crash of any kind must close the run as an error, never leave it running or approved.
        try:
            await self._read(run_id, filename, source, trace)
            await self._identify(run_id, trace)
            async with self._vendor_lock(trace):
                await self._check(run_id, trace)
                await self._finish(run_id, trace, started)
        except Exception as error:
            _Helper.LOGGER.exception("run %s failed at %s", run_id, trace.stage)
            await self._fail(run_id, trace, error, started)

    def _vendor_lock(self, trace: _Types.Trace) -> contextlib.AbstractAsyncContextManager[Any]:
        """Return the matched vendor's lock, or no lock when no vendor was matched."""
        vendor_id = trace.vendor.vendor_id if trace.vendor else None
        if vendor_id is None:
            return contextlib.nullcontext()

        return self._vendor_locks.setdefault(vendor_id, asyncio.Lock())

    async def _step[R: BaseModel](
        self,
        run_id: str,
        name: StageTypes.StepName,
        trace: _Types.Trace,
        work: Callable[[], R | Awaitable[R]],
    ) -> R:
        """Announce a stage, run it, store its result as a done step and publish it."""
        trace.stage = name
        self._run_entity.publish(
            StageTypes.StepEvent(run_id=run_id, name=name, state="running", millis=0, data={}, findings=[]), run_id
        )
        started = time.perf_counter()
        outcome = work()
        result: R = await outcome if inspect.isawaitable(outcome) else outcome
        findings: list[FindingTypes.Finding] = getattr(result, "findings", [])
        data = result.model_dump(mode="json", exclude={"findings"})
        await self._run_entity.record(
            StageTypes.StepEvent(
                run_id=run_id, name=name, state="done", millis=_Helper.millis(started), data=data, findings=findings
            )
        )
        return result

    async def _skip(self, run_id: str, names: tuple[StageTypes.StepName, ...], reason: str) -> None:
        """Store and publish the named stages as skipped."""
        for name in names:
            await self._run_entity.record(
                StageTypes.StepEvent(
                    run_id=run_id, name=name, state="skipped", millis=0, data={"reason": reason}, findings=[]
                )
            )

    async def _read(
        self, run_id: str, filename: str | None, source: bytes | ExtractionTypes.InvoiceRead, trace: _Types.Trace
    ) -> None:
        """Stage 0 intake and stage 1 extraction, or skip intake and convert a typed invoice."""
        if isinstance(source, ExtractionTypes.InvoiceRead):
            await self._skip(run_id, ("intake",), "Entered manually; there is no file.")
            trace.extraction = await self._step(run_id, "extraction", trace, lambda: self._extraction.typed(source))
            return

        async def intake() -> StageTypes.IntakeResult:
            result, trace.media_type = await self._intake.run(run_id, filename, source)
            return result

        trace.intake = await self._step(run_id, "intake", trace, intake)
        if not trace.intake.accepted or trace.media_type is None:
            return

        media_type = trace.media_type
        trace.extraction = await self._step(
            run_id, "extraction", trace, lambda: self._extraction.extract(source, media_type, filename)
        )

    async def _identify(self, run_id: str, trace: _Types.Trace) -> None:
        """Stage 2 validation, stage 3 vendor and stage 4 bank: only the document and the vendor master, no lock."""
        reading = trace.extraction.extraction if trace.extraction else None
        if reading is None:
            reason = "The file was rejected at intake." if trace.extraction is None else "Nothing was extracted."
            stages: tuple[StageTypes.StepName, ...] = ("validation", "vendor", "bank", "duplicate", "po", "amounts")
            await self._skip(run_id, stages if trace.extraction else ("extraction", *stages), reason)
            return

        trace.validation = await self._step(
            run_id, "validation", trace, lambda: self._validation.run(reading, date.today())
        )
        trace.vendor = await self._step(
            run_id, "vendor", trace, lambda: self._vendor.run(reading.supplier, reading.supplier_bank)
        )
        vendor_id = trace.vendor.vendor_id
        if vendor_id is None:
            await self._skip(run_id, ("bank",), "The vendor is unknown, so there is no master account to compare.")
        else:
            trace.bank = await self._step(
                run_id, "bank", trace, lambda: self._bank.run(vendor_id, reading.supplier_bank)
            )

    async def _check(self, run_id: str, trace: _Types.Trace) -> None:
        """Stage 5 duplicate, stage 6 PO (with Jev) and stage 7 amounts, under the matched vendor's lock."""
        reading = trace.extraction.extraction if trace.extraction else None
        validation, vendor = trace.validation, trace.vendor
        if reading is None or validation is None or vendor is None:
            return

        override = await self._vendor_entity.tolerance_pct(vendor.vendor_id) if vendor.vendor_id else None
        pct = override if override is not None else self._rules_entity.tolerance_pct
        priced = self._validation.priced_lines(reading, validation)
        taxable = validation.taxable_value

        vendor_id = vendor.vendor_id
        if vendor_id is None:
            await self._skip(run_id, ("duplicate",), "The vendor is unknown.")
        else:
            trace.duplicate = await self._step(
                run_id, "duplicate", trace, lambda: self._duplicate.run(vendor_id, reading, taxable)
            )

        earlier = trace.findings()
        trace.order = await self._step(
            run_id,
            "po",
            trace,
            lambda: self._po.run(reading, vendor, priced, taxable=taxable, earlier=earlier),
        )

        order = trace.order
        snapshots = await self._amounts.snapshots(order)
        reason = self._amounts.skip_reason(order, snapshots, taxable)
        if reason is not None or taxable is None:
            await self._skip(run_id, ("amounts",), reason or "There is no taxable value to compare.")
            return

        trace.amounts = await self._step(
            run_id,
            "amounts",
            trace,
            lambda: self._amounts.run(
                order,
                snapshots,
                taxable,
                reading=reading,
                priced=priced,
                pct=pct,
                invoice_rate=validation.tax_rate_pct,
            ),
        )

    async def _finish(self, run_id: str, trace: _Types.Trace, started: float) -> None:
        """Stage 8 decision and stage 9 persist (one transaction), then announce the run's end."""
        po_number = trace.order.po_number if trace.order else None
        findings = trace.findings()
        decision = await self._step(
            run_id,
            "decision",
            trace,
            lambda: self._decision.run(
                findings, trace.amounts, po_number, vendor=trace.vendor, bank=trace.bank, order=trace.order
            ),
        )

        trace.stage = "persist"
        self._run_entity.publish(
            StageTypes.StepEvent(run_id=run_id, name="persist", state="running", millis=0, data={}, findings=[]),
            run_id,
        )
        closing = self._closing(
            trace, status="completed", outcome=decision.outcome, summary=decision.summary, started=started
        )
        event = await self._persist.run(
            run_id,
            reading=trace.extraction.extraction if trace.extraction else None,
            taxable=trace.validation.taxable_value if trace.validation else None,
            decision=decision,
            closing=closing,
        )
        if event is None:
            _Helper.LOGGER.info("run %s was deleted by a reset before it was stored", run_id)
            return

        self._run_entity.publish(event, run_id)
        self._run_entity.announce(run_id, closing)

    async def _fail(self, run_id: str, trace: _Types.Trace, error: Exception, started: float) -> None:
        """Mark the current step failed and close the run as an error."""
        failed = StageTypes.StepEvent(
            run_id=run_id,
            name=trace.stage,
            state="failed",
            millis=0,
            data={"error": f"{type(error).__name__}: {error}"},
            findings=[],
        )
        summary = f"The run failed unexpectedly at the {trace.stage} stage; nothing was approved."
        closing = self._closing(trace, status="error", outcome=None, summary=summary, started=started)

        # When even the failure cannot be stored, followers must still get done; startup closes the stored row.
        try:
            await self._run_entity.record(failed)
            await self._run_entity.close(run_id, closing)
        except Exception:
            _Helper.LOGGER.exception("could not store the failure of run %s", run_id)
            self._run_entity.publish(failed, run_id)
            self._run_entity.announce(run_id, closing)

    def _closing(
        self,
        trace: _Types.Trace,
        *,
        status: StageTypes.RunStatus,
        outcome: StageTypes.Outcome | None,
        summary: str,
        started: float,
    ) -> StageTypes.RunClosing:
        """Build the record written on a run when it ends; the persist stage links the register row separately."""
        return StageTypes.RunClosing(
            status=status,
            outcome=outcome,
            summary=summary,
            vendor_id=trace.vendor.vendor_id if trace.vendor else None,
            po_number=trace.order.po_number if trace.order else None,
            millis=_Helper.millis(started),
        )
