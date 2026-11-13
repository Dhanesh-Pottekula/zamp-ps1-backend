"""
Show, sample by sample, whether the PO stage hands invoice lines to Jev, and what Jev answers.

Always runs on a throwaway database and upload folder (the demo data is never touched) and always uses the saved
readings of the samples (no extraction model is called). Jev:

- no TYPESAFE_API_KEY (or --spy): a spy records each request and answers nothing; the report shows who would be
  asked, about which lines and how many questions, and the lines Jev was not asked about are the code's own matches;
- TYPESAFE_API_KEY set: real Jev is called and the report shows its answers and what the code did with them.

Usage:
    uv run python scripts/jev_trigger_report.py              # spy without a key, live Jev with one
    uv run python scripts/jev_trigger_report.py --spy        # never call Jev, even with a key
    uv run python scripts/jev_trigger_report.py --show INV-039   # also print that sample's full Jev request
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_TEMP = Path(tempfile.mkdtemp(prefix="jev-report-"))
os.environ.update(
    {
        "STORAGE_DB_PATH": str(_TEMP / "report.sqlite3"),
        "STORAGE_UPLOAD_DIR": str(_TEMP / "uploads"),
        "LLM_MODE": "recorded",
        "JEV_RECORD": "false",
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters import AdapterRegistry  # noqa: E402 - the settings above must be in place before app imports
from app.config import ENVConfigs  # noqa: E402
from app.entities import EntitiesRegistry, StageTypes  # noqa: E402


class _Spy:
    """Stands in for the Jev adapter without a key: records the request and answers no to everything."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def evaluate(self, state: dict[str, Any], questions: dict[str, Any], *, timeout: float) -> dict[str, float]:  # noqa: ASYNC109
        self.calls.append((state, questions))
        return dict.fromkeys(questions, 0.02)


class _Report:
    """Run the samples and describe each one's Jev request."""

    def __init__(self, spy: bool) -> None:
        self.spy = spy
        self.requests: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, float] | None, str | None]] = {}

    async def run(self, show: str | None) -> int:
        adapter = AdapterRegistry()
        await EntitiesRegistry.initialize(adapter)
        jev = EntitiesRegistry.Jev
        if self.spy or adapter.Jev is None:
            jev._jev_adapter = _Spy()  # type: ignore[assignment]
            mode = "spy (Jev is not called; every answer is 'no')"
        else:
            mode = f"LIVE Jev ({ENVConfigs.Jev.model})"

        current = {"id": ""}
        original_ask = jev.ask

        async def ask(state: dict[str, Any], questions: dict[str, Any], key: dict[str, Any] | None = None) -> Any:
            answers, reason, digest = await original_ask(state, questions, key)
            self.requests[current["id"]] = (state, questions, answers, reason)
            return answers, reason, digest

        jev.ask = ask  # type: ignore[method-assign]
        rows = []
        try:
            await EntitiesRegistry.Pipeline.reset()
            for sample in await EntitiesRegistry.Seed.samples():
                current["id"] = sample.id
                rows.append(await self.sample(sample.id))
        finally:
            await EntitiesRegistry.Pipeline.close()
            await adapter.close()

        print(f"Jev: {mode}. Readings: saved (no model). Database: {_TEMP}")
        head = f"{'sample':8} {'Jev?':5} {'lines asked':12} {'qs':>3}  {'outcome':16}"
        print(f"{head} how each line was settled / why Jev was not asked")
        for row in rows:
            cells = f"{row['id']:8} {row['asked']:5} {row['lines']:12} {row['questions']:>3}  {row['outcome']:16}"
            print(f"{cells} {row['note']}")
        asked = [row["id"] for row in rows if row["asked"] == "YES"]
        print(f"\nJev asked for {len(asked)} of {len(rows)} samples: {', '.join(asked) or 'none'}")
        if show:
            self.dump(show)
        return 0

    async def sample(self, sample_id: str) -> dict[str, Any]:
        sample = await EntitiesRegistry.Seed.sample(sample_id)
        assert sample is not None
        run_id = await EntitiesRegistry.Pipeline.submit(f"{sample_id}.pdf", sample.pdf, sample_id)
        async for item in EntitiesRegistry.Run.follow(run_id):
            if isinstance(item, StageTypes.RunClosed):
                break

        detail = await EntitiesRegistry.Run.detail(run_id)
        assert detail is not None
        steps = {step.name: step for step in detail.steps}
        po = steps.get("po")
        request = self.requests.get(sample_id)
        row = {"id": sample_id, "outcome": detail.outcome or "-", "asked": "no", "lines": "-", "questions": 0}
        if request:
            _, questions, answers, reason = request
            lines = sorted({int(name.removeprefix("line_").split("__")[0]) for name in questions})
            row |= {"asked": "YES", "lines": ",".join(map(str, lines)), "questions": len(questions)}
            if answers is None:
                row["note"] = f"Jev gave no usable answer: {reason}"
                return row

        row["note"] = self.note(po, steps)
        return row

    @staticmethod
    def note(po: Any, steps: dict[str, Any]) -> str:
        if po is None or po.state != "done":
            return "PO stage skipped (" + (po.data.get("reason", "") if po else "no PO step") + ")"

        allocations = po.data.get("allocations", [])
        if not allocations:
            blocks = [finding.code for finding in po.findings if finding.severity == "block"]
            return "no lines matched: " + ", ".join(blocks)

        parts = []
        for allocation in allocations:
            target = f"{allocation['po_number']}/{allocation['po_line']}" if allocation["po_number"] else "-"
            parts.append(f"L{allocation['line']}→{target} {allocation['method']}")
        earlier = [
            finding.code
            for name in ("validation", "vendor", "bank", "duplicate")
            if name in steps
            for finding in steps[name].findings
            if finding.severity == "block"
        ]
        suffix = f"  (earlier block, Jev kept out: {', '.join(earlier)})" if earlier else ""
        return "; ".join(parts) + suffix

    def dump(self, sample_id: str) -> None:
        request = self.requests.get(sample_id)
        if request is None:
            print(f"\n{sample_id}: Jev was not asked.")
            return

        state, questions, answers, reason = request
        print(f"\n=== {sample_id}: request sent to Jev ===")
        print(json.dumps({"state": state, "questions": questions}, indent=2))
        print(f"=== {sample_id}: answers ({reason or 'ok'}) ===")
        print(json.dumps(answers, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spy", action="store_true", help="never call Jev, even when TYPESAFE_API_KEY is set")
    parser.add_argument("--show", metavar="INV-NNN", help="print that sample's full Jev request and answers")
    arguments = parser.parse_args()
    return asyncio.run(_Report(arguments.spy).run(arguments.show))


if __name__ == "__main__":
    sys.exit(main())
