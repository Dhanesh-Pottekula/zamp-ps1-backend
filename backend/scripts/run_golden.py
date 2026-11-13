"""
Reset the demo data, run the samples in step order through the real pipeline and compare with data/golden.json.

Uses whatever extraction mode is configured (recorded without a key, live with ANTHROPIC_API_KEY or
OPENAI_API_KEY), whatever Jev mode is configured (recorded without TYPESAFE_API_KEY; JEV_RECORD=true writes live
answers) and the configured database and upload folder, which the reset empties. To keep the demo data, point
STORAGE_DB_PATH and STORAGE_UPLOAD_DIR at a temporary folder. A recorded Jev answer replays whenever the same POs of
the vendor are open, so a sample run out of order may find none. Prints a pass / fail table; exits 1 on any
failure.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Final

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters import AdapterRegistry
from app.entities import EntitiesRegistry, StageTypes


class _Types:
    """Expected and actual results of one sample."""

    class Expected(BaseModel):
        """One row of golden.json."""

        id: str
        outcome: StageTypes.Outcome
        codes: list[str]
        vendor: str | None
        po: str | None
        left: str | None

    class Actual(BaseModel):
        """What the pipeline produced for one sample."""

        outcome: StageTypes.Outcome | None
        codes: list[str]
        vendor: str | None
        po: str | None
        left: str | None
        summary: str | None


class GoldenRunner:
    """Run the scenario and compare it with the golden expectations."""

    GOLDEN: Final = Path(__file__).resolve().parents[1] / "data" / "golden.json"

    @staticmethod
    async def run_sample(sample_id: str) -> _Types.Actual:
        """Run one sample, wait for its closing event and collect the compared facts."""
        sample = await EntitiesRegistry.Seed.sample(sample_id)
        if sample is None:
            message = f"sample {sample_id} is not in the samples table"
            raise RuntimeError(message)

        run_id = await EntitiesRegistry.Pipeline.submit(f"{sample_id}.pdf", sample.pdf, sample_id)
        async for item in EntitiesRegistry.Run.follow(run_id):
            if isinstance(item, StageTypes.RunClosed):
                break

        detail = await EntitiesRegistry.Run.detail(run_id)
        if detail is None:
            message = f"run {run_id} disappeared"
            raise RuntimeError(message)

        order = await EntitiesRegistry.Order.snapshot(detail.po_number) if detail.po_number else None
        return _Types.Actual(
            outcome=detail.outcome,
            codes=[finding.code for step in detail.steps for finding in step.findings],
            vendor=detail.vendor_id,
            po=detail.po_number,
            left=f"{order.remaining:.2f}" if order else None,
            summary=detail.summary,
        )

    @staticmethod
    def failures(expected: _Types.Expected, actual: _Types.Actual) -> list[str]:
        """Return what differs from the expectation."""
        missing = [code for code in expected.codes if code not in actual.codes]
        checks = (
            ("outcome", expected.outcome, actual.outcome),
            ("vendor", expected.vendor, actual.vendor),
            ("po", expected.po, actual.po),
            ("left", expected.left, actual.left),
        )
        problems = [f"{name} {got!r} != {want!r}" for name, want, got in checks if want != got]
        return problems + ([f"missing codes {missing}"] if missing else [])

    @staticmethod
    async def evaluate() -> list[tuple[_Types.Expected, _Types.Actual, list[str]]]:
        """Reset, run every sample of the samples table in step order and compare each with its golden row."""
        golden = (_Types.Expected.model_validate(row) for row in json.loads(GoldenRunner.GOLDEN.read_text()))
        expectations = {expected.id: expected for expected in golden}
        await EntitiesRegistry.Pipeline.reset()
        results: list[tuple[_Types.Expected, _Types.Actual, list[str]]] = []
        for sample in await EntitiesRegistry.Seed.samples():
            expected = expectations[sample.id]
            actual = await GoldenRunner.run_sample(expected.id)
            results.append((expected, actual, GoldenRunner.failures(expected, actual)))

        return results


async def main() -> int:
    """Run the scenario in the configured mode and print the table."""
    adapter = AdapterRegistry()
    await EntitiesRegistry.initialize(adapter)
    try:
        results = await GoldenRunner.evaluate()
    finally:
        await EntitiesRegistry.Pipeline.close()
        await adapter.close()

    print(f"Mode: {EntitiesRegistry.ExtractionStage.mode} ({EntitiesRegistry.ExtractionStage.model})")
    print(f"{'sample':8} {'result':5} {'outcome':16} {'vendor':6} {'po':13} {'left':10} notes")
    for expected, actual, problems in results:
        verdict = "PASS" if not problems else "FAIL"
        notes = "; ".join(problems) or (actual.summary or "")
        row = f"{expected.id:8} {verdict:5} {actual.outcome or '-':16} {actual.vendor or '-':6} "
        print(f"{row}{actual.po or '-':13} {actual.left or '-':10} {notes}")

    failed = sum(bool(problems) for _, _, problems in results)
    print(f"{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
