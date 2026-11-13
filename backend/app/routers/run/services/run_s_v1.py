"""
Version 1 run service: start runs from uploads, samples or typed invoices, list them, and stream their steps.

A run stores ids only: the vendor's legal name and the invoice's number and taxable value are read from their own
tables, the token usage from the extraction step, and the detail's reasons are the stored step findings in reason
order.
"""

import asyncio
import functools
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from app.entities import EntitiesRegistry, ExtractionTypes, StageTypes
from app.routers.run.run_models import RunModels

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Sequence
    from pathlib import Path

    from fastapi import UploadFile

    from app.entities import InvoiceTypes, RunTypes


class RunServiceV1Class:
    """Orchestrate run flows through the pipeline, run, seed, vendor, invoice and rules entities."""

    def __init__(self) -> None:
        """Capture the shared entities."""
        self._pipeline_entity = EntitiesRegistry.Pipeline
        self._run_entity = EntitiesRegistry.Run
        self._seed_entity = EntitiesRegistry.Seed
        self._vendor_entity = EntitiesRegistry.Vendor
        self._invoice_entity = EntitiesRegistry.Invoice
        self._rules_entity = EntitiesRegistry.Rules

    async def upload(self, file: UploadFile) -> RunModels.V1.Created:
        """Accept an uploaded document; reading stops one byte past the size limit so intake can reject it."""
        data = await file.read(size=self._pipeline_entity.max_bytes + 1)
        run_id = await self._pipeline_entity.submit(file.filename, data, None)
        return RunModels.V1.Created(id=run_id)

    async def sample(self, sample_id: str) -> RunModels.V1.Created:
        """
        Start a run on a sample invoice, at any time and in any order.

        Raises:
            HTTPException: 404 for an unknown sample.

        """
        sample = await self._seed_entity.sample(sample_id)
        if sample is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown sample {sample_id}")

        run_id = await self._pipeline_entity.submit(None, sample.pdf, sample_id)
        return RunModels.V1.Created(id=run_id)

    async def manual(self, invoice: RunModels.V1.Manual) -> RunModels.V1.Created:
        """Start a run on a typed invoice; it has no source quotes and skips intake, and every rule still applies."""
        reading = ExtractionTypes.InvoiceRead.model_validate({**invoice.model_dump(), "sources": []})
        return RunModels.V1.Created(id=await self._pipeline_entity.enter(reading))

    async def history(self, outcome: StageTypes.Outcome | None) -> RunModels.V1.Runs:
        """Return runs newest first, optionally filtered by outcome."""
        runs = await self._run_entity.history(outcome)
        vendors, invoices = self._reads(runs)
        names, briefs, usages = await asyncio.gather(
            vendors, invoices, self._run_entity.usages([run.id for run in runs])
        )
        return RunModels.V1.Runs(data=[self._row(run, names, briefs, usages.get(run.id)) for run in runs])

    async def detail(self, run_id: str) -> RunModels.V1.Detail:
        """
        Return one run with its steps and every step finding in reason order; a running run has no reasons yet.

        Raises:
            HTTPException: 404 when the run does not exist.

        """
        run = await self._run_entity.detail(run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

        vendors, invoices = self._reads([run])
        names, briefs = await asyncio.gather(vendors, invoices)
        row = self._row(run, names, briefs, run.usage)
        findings = [finding for step in run.steps for finding in step.findings]
        reasons = [] if run.status == "running" else self._rules_entity.ordered(findings)
        return RunModels.V1.Detail.model_validate(
            {
                **row.model_dump(),
                "reasons": [finding.model_dump() for finding in reasons],
                "steps": [self._step(step).model_dump() for step in run.steps],
            }
        )

    async def document(self, run_id: str) -> tuple[Path, str]:
        """
        Return the path and media type of the document a run was started from.

        Raises:
            HTTPException: 404 when the run has no stored document (unknown run, typed invoice or rejected file).

        """
        found = await self._pipeline_entity.document(run_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This run has no stored document")

        return found

    async def events(self, run_id: str) -> AsyncIterator[str]:
        """
        Return the run's event stream in text/event-stream framing.

        Raises:
            HTTPException: 404 when the run does not exist.

        """
        if await self._run_entity.detail(run_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

        return self._frames(self._run_entity.follow(run_id))

    async def _frames(self, items: AsyncIterator[RunTypes.StreamItem]) -> AsyncIterator[str]:
        """Frame each step as `event: step` and the closing event as `event: done`."""
        async for item in items:
            if isinstance(item, StageTypes.StepEvent):
                yield f"event: step\ndata: {self._step(item).model_dump_json(by_alias=True)}\n\n"
                continue

            done = RunModels.V1.Done(status=item.status, outcome=item.outcome, summary=item.summary)
            yield f"event: done\ndata: {done.model_dump_json(by_alias=True)}\n\n"

    @staticmethod
    def _step(step: StageTypes.StepEvent) -> RunModels.V1.Step:
        """Turn a step event into its wire row."""
        return RunModels.V1.Step.model_validate(
            step.model_dump(include={"name", "state", "millis", "data", "findings"})
        )

    def _reads(
        self, runs: Sequence[RunTypes.RunView]
    ) -> tuple[Awaitable[dict[str, str]], Awaitable[dict[int, InvoiceTypes.BriefFetch]]]:
        """Return the unstarted reads of the vendor names and invoice briefs the runs reference, one query each."""
        return (
            self._vendor_entity.names({run.vendor_id for run in runs if run.vendor_id is not None}),
            self._invoice_entity.briefs({run.invoice_id for run in runs if run.invoice_id is not None}),
        )

    @staticmethod
    def _row(
        run: RunTypes.RunView,
        names: dict[str, str],
        briefs: dict[int, InvoiceTypes.BriefFetch],
        usage: StageTypes.Usage | None,
    ) -> RunModels.V1.Run:
        """Turn a run view into its wire row with its vendor name, invoice brief and token usage filled in."""
        brief = briefs.get(run.invoice_id) if run.invoice_id is not None else None
        return RunModels.V1.Run(
            id=run.id,
            filename=run.filename,
            sample=run.sample,
            status=run.status,
            outcome=run.outcome,
            vendor_id=run.vendor_id,
            vendor_name=names.get(run.vendor_id) if run.vendor_id is not None else None,
            po_number=run.po_number,
            invoice_id=run.invoice_id,
            invoice_number=brief.invoice_number if brief else None,
            amount=brief.taxable if brief else None,
            currency=brief.currency if brief else None,
            summary=run.summary,
            tokens=usage.model_dump() if usage else None,
            mode=run.mode,
            created_at=run.created_at,
            millis=run.millis,
        )


@functools.cache
def get_run_service_singleton_v1() -> RunServiceV1Class:
    """Return the shared version 1 run service."""
    return RunServiceV1Class()
