"""Jev PO selection: settings, trigger, request building, recordings, selection and the pipeline fallback."""

import json
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from conftest import DATA, reading
from test_master import post
from test_pipeline import stream

from app.config import ENVConfigs
from app.entities import EntitiesRegistry, FindingTypes, StageTypes
from app.entities.stages.po_jev import PoJevClass

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

    from app.adapters import AdapterRegistry


class FakeJev:
    """Answer each line question from a table keyed "PO-…/k" (any line) or "n:PO-…/k" (line n), or fail."""

    def __init__(
        self, answers: dict[str, float] | None = None, *, default: float = 0.02, error: Exception | None = None
    ) -> None:
        self.answers = answers or {}
        self.default = default
        self.error = error
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def evaluate(
        self,
        state: dict[str, Any],
        questions: dict[str, Any],
        *,
        timeout: float,  # noqa: ASYNC109 - same signature as the real adapter
    ) -> dict[str, float]:
        self.calls.append((state, questions))
        if self.error is not None:
            raise self.error

        answers: dict[str, float] = {}
        for name in questions:
            line, po_number, po_line = name.removeprefix("line_").split("__")
            key = f"{po_number}/{po_line}"
            answers[name] = self.answers.get(f"{line}:{key}", self.answers.get(key, self.default))
        return answers


def finding(code: str, severity: str = "block", **data: str) -> FindingTypes.Finding:
    return FindingTypes.Finding.of(code, severity, f"{code} for the test", **data)  # type: ignore[arg-type]


def snapshot(number: str, day: int, *lines: tuple[str, str, str], status: str = "open") -> StageTypes.OrderSnapshot:
    """A PO dated 2024-10-<day> whose lines are (description, ordered, consumed) at 1,000.00 and GST 18%."""
    rows = [
        StageTypes.OrderLine(
            line_no=index,
            description=description,
            quantity=Decimal(ordered),
            unit="pcs",
            unit_price=Decimal(1000),
            tax_rate_pct=Decimal(18),
            consumed_qty=Decimal(consumed),
        )
        for index, (description, ordered, consumed) in enumerate(lines, start=1)
    ]
    total = sum((row.quantity * row.unit_price for row in rows), Decimal(0))
    consumed = sum((row.consumed_qty * row.unit_price for row in rows), Decimal(0))
    return StageTypes.OrderSnapshot(
        po_number=number,
        vendor_id="V010",
        vendor_name="Brightline Infotech Pvt Ltd",
        po_date=date(2024, 10, day),
        currency="INR",
        status=status,  # type: ignore[arg-type]
        total=total,
        consumed=consumed,
        remaining=total - consumed,
        lines=rows,
    )


def entity(adapter: object | None = None, recordings: Path | None = None, *, record: bool = False) -> PoJevClass:
    """Jev with the user's bands (selected above 0.80, rejected at or below 0.50)."""
    return PoJevClass(
        adapter,  # type: ignore[arg-type]
        recordings=recordings or DATA / "missing-recordings.json",
        record=record,
        select_above=0.80,
        reject_at_or_below=0.50,
        timeout=10.0,
    )


async def test_jev_is_recorded_in_tests(adapter: AdapterRegistry) -> None:
    assert ENVConfigs.Jev.active == "recorded"
    assert adapter.Jev is None
    assert (ENVConfigs.Jev.select_above, ENVConfigs.Jev.reject_at_or_below) == (0.80, 0.50)


# Recordings and failures


async def test_recorded_answers_replay_by_digest_and_misses_are_reported(tmp_path: Path) -> None:
    store = tmp_path / "recordings.json"
    live = entity(FakeJev({"PO-1/1": 0.9}), store, record=True)
    state, questions = {"situation": "s"}, {"line_1__PO-1__1": {"type": "noul"}}

    answers, reason, digest = await live.ask(state, questions)
    replayed = await entity(None, store).ask(state, questions)
    missed = await entity(None, store).ask({"situation": "other"}, questions)

    assert (answers, reason) == ({"line_1__PO-1__1": 0.9}, None)
    assert json.loads(store.read_text())[digest]["answers"] == {"line_1__PO-1__1": 0.9}
    assert replayed == (answers, None, digest)
    assert missed[0] is None
    assert "no recorded Jev answer" in (missed[1] or "")


async def test_a_corrupt_recordings_file_is_a_skip_not_a_crash(tmp_path: Path) -> None:
    store = tmp_path / "recordings.json"
    store.write_text("{not json")
    answers, reason, _ = await entity(None, store).ask({}, {"line_1__PO-1__1": {}})
    assert answers is None
    assert "could not be read" in (reason or "")


async def test_a_recording_that_cannot_be_written_never_fails_a_live_answer(tmp_path: Path) -> None:
    store = tmp_path / "recordings.json"
    store.write_text("{not json")

    answers, reason, _ = await entity(FakeJev({"PO-1/1": 0.9}), store, record=True).ask({}, {"line_1__PO-1__1": {}})

    assert (answers, reason) == ({"line_1__PO-1__1": 0.9}, None)


async def test_a_jev_error_is_a_skip() -> None:
    answers, reason, _ = await entity(FakeJev(error=TimeoutError("slow"))).ask({}, {"line_1__PO-1__1": {}})
    assert answers is None
    assert reason == "Jev could not be reached: TimeoutError"


# Pipeline helpers


def brightline(**changes: object) -> dict[str, Any]:
    """INV-028's reading typed by hand without its PO reference: headsets of PO-2024-0159, desks of PO-2024-0160."""
    raw = json.loads((DATA / "invoices" / "INV-028.read.json").read_text())
    raw.pop("sources")
    return raw | {"po_reference_text": ""} | changes


async def run(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    status, created = await post(client, "/v1/runs/manual", body)
    assert status == 202
    assert (await stream(client, created["id"]))[-1][0] == "done"
    return (await client.get(f"/v1/runs/{created['id']}")).json()


def codes(detail: dict[str, Any]) -> set[str]:
    return {reason["code"] for reason in detail["reasons"]}


def step(detail: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((item for item in detail["steps"] if item["name"] == name), None)


async def balances(client: httpx.AsyncClient) -> dict[str, str]:
    return {order["po"]: order["left"] for order in (await client.get("/v1/orders")).json()["data"]}


@pytest.fixture
def jev(monkeypatch: pytest.MonkeyPatch) -> FakeJev:
    """A fake Jev on the pipeline's own Assist instance; tests set its answers."""
    fake = FakeJev()
    monkeypatch.setattr(EntitiesRegistry.Jev, "_jev_adapter", fake)
    return fake


# Pipeline: line matching end to end (manual invoices against Brightline's POs 0159 and 0160)


def cordless(**changes: object) -> dict[str, Any]:
    """Brightline invoice, no PO printed: 'Cordless headset' scores 81 against 'Wireless headset' (not sure)."""
    lines = brightline()["lines"]
    lines[0] = lines[0] | {"description": "Cordless headset"}
    return brightline(lines=lines) | changes


async def test_exact_lines_on_two_pos_match_by_code_without_jev(client: httpx.AsyncClient, jev: FakeJev) -> None:
    before = await balances(client)

    detail = await run(client, brightline())

    po = step(detail, "po")
    assert po is not None
    assert detail["outcome"] == "approved"
    assert (po["data"]["method"], po["data"]["po_number"], po["data"]["others"]) == (
        "lines",
        "PO-2024-0159",
        ["PO-2024-0160"],
    )
    assert jev.calls == []
    after = await balances(client)
    assert Decimal(before["PO-2024-0159"]) - Decimal(after["PO-2024-0159"]) == Decimal(64000)
    assert Decimal(before["PO-2024-0160"]) - Decimal(after["PO-2024-0160"]) == Decimal(135000)


async def test_jev_keeps_the_code_suggestion(client: httpx.AsyncClient, jev: FakeJev) -> None:
    jev.answers = {"1:PO-2024-0159/4": 0.96}

    detail = await run(client, cordless())

    po = step(detail, "po")
    assert po is not None
    assert detail["outcome"] == "approved"
    assert po["data"]["method"] == "jev"
    assert [(item["line"], item["method"]) for item in po["data"]["allocations"]] == [(1, "jev_kept"), (2, "code")]
    assert {"LINES_MATCHED_BY_JEV", "PO_MATCHED"} <= codes(detail)
    state, questions = jev.calls[0]
    assert all(name.startswith("line_1__") for name in questions)  # only the not-sure line is asked about
    assert len(questions) == 7  # every line of PO-0159, 0160 and 0161 with something left, no cap
    assert state["code_conclusions"][0]["suggestion"] == "PO-2024-0159/4"


async def test_jev_changes_the_line_and_the_code_still_checks_the_money(
    client: httpx.AsyncClient, jev: FakeJev
) -> None:
    jev.answers = {"1:PO-2024-0159/3": 0.95}

    detail = await run(client, cordless())

    po = step(detail, "po")
    assert po is not None
    assert po["data"]["allocations"][0]["method"] == "jev_changed"
    assert (po["data"]["allocations"][0]["po_number"], po["data"]["allocations"][0]["po_line"]) == ("PO-2024-0159", 3)
    assert detail["outcome"] == "needs_attention"  # 3,200 billed against the monitor line's 11,000
    assert "UNIT_PRICE_MISMATCH" in codes(detail)


async def test_jev_drops_the_line_and_the_invoice_is_held(client: httpx.AsyncClient, jev: FakeJev) -> None:
    jev.answers = {}
    before = await balances(client)

    detail = await run(client, cordless())

    po = step(detail, "po")
    assert po is not None
    assert po["data"]["allocations"][0]["method"] == "jev_dropped"
    assert detail["outcome"] == "needs_attention"
    assert "LINES_NOT_MATCHED" in codes(detail)
    assert step(detail, "amounts")["state"] == "skipped"  # type: ignore[index]
    assert await balances(client) == before


async def test_an_unsure_jev_holds_the_line_and_shows_its_answers(client: httpx.AsyncClient, jev: FakeJev) -> None:
    jev.answers = {"1:PO-2024-0159/4": 0.70}

    detail = await run(client, cordless())

    blocks = {reason["code"]: reason for reason in detail["reasons"] if reason["level"] == "block"}
    assert "LINES_NOT_MATCHED" in blocks
    assert "PO-2024-0159/4 (0.70)" in blocks["LINES_NOT_MATCHED"]["text"]


async def test_an_earlier_block_keeps_jev_out_but_sure_lines_match(client: httpx.AsyncClient, jev: FakeJev) -> None:
    detail = await run(client, cordless(bank_account_number="11112222333344"))

    po = step(detail, "po")
    assert po is not None
    assert jev.calls == []
    assert [item["method"] for item in po["data"]["allocations"]] == ["unmatched", "code"]
    assert {"BANK_ACCOUNT_MISMATCH", "LINES_NOT_MATCHED"} <= codes(detail)


async def test_a_jev_failure_leaves_the_unsure_line_unmatched(client: httpx.AsyncClient, jev: FakeJev) -> None:
    jev.error = TimeoutError("slow")

    detail = await run(client, cordless())

    po = step(detail, "po")
    assert po is not None
    assert detail["outcome"] == "needs_attention"
    assert po["data"]["assist"]["state"] == "skipped"
    assert po["data"]["assist"]["reason"] == "Jev could not be reached: TimeoutError"
    assert (po["data"]["assist"]["mode"], len(po["data"]["assist"]["digest"])) == ("live", 64)


async def test_without_a_recording_jev_is_skipped(client: httpx.AsyncClient) -> None:
    detail = await run(client, cordless())

    po = step(detail, "po")
    assert po is not None
    assert detail["outcome"] == "needs_attention"
    assert "no recorded Jev answer" in po["data"]["assist"]["reason"]


def test_a_line_on_two_po_lines_fills_the_oldest_first(registry: type[EntitiesRegistry]) -> None:
    item = StageTypes.LinePlan(
        line=1, description="chairs", quantity=Decimal(100), sure=False, suggestion=None, rivals=[], ask=[]
    )
    newer, older = candidate("PO-2", 1, 90, left="50", day=9), candidate("PO-1", 1, 90, left="50", day=1)

    portions = registry.PoStage.allocate(item, [newer, older], "jev_changed", {})
    covered = registry.PoStage.allocate(
        item.model_copy(update={"quantity": Decimal(40)}), [newer, older], "jev_changed", {}
    )
    over = registry.PoStage.allocate(
        item.model_copy(update={"quantity": Decimal(120)}), [newer, older], "jev_changed", {}
    )

    assert [(p.po_number, p.quantity) for p in portions] == [("PO-1", Decimal(50)), ("PO-2", Decimal(50))]
    assert [(p.po_number, p.quantity) for p in covered] == [("PO-1", Decimal(40))]  # no zero portion
    assert [(p.po_number, p.quantity) for p in over] == [("PO-1", Decimal(50)), ("PO-2", Decimal(70))]


def test_a_split_without_a_quantity_is_unmatched(registry: type[EntitiesRegistry]) -> None:
    item = StageTypes.LinePlan(line=1, description="x", quantity=None, sure=False, suggestion=None, rivals=[], ask=[])

    portions = registry.PoStage.allocate(item, [candidate("PO-1", 1, 90), candidate("PO-2", 1, 90)], "jev_changed", {})

    assert [p.method for p in portions] == ["unmatched"]


# Line matching (Jev decides the invoice lines the code is not sure about)


def candidate(po: str, line: int, score: float, left: str = "5", day: int = 1) -> StageTypes.LineCandidate:
    return StageTypes.LineCandidate(
        po_number=po,
        po_line=line,
        po_date=date(2024, 10, day),
        description=f"item {line}",
        score=score,
        left=Decimal(left),
    )


def plan(line: int, *, sure: bool, ask: list[StageTypes.LineCandidate]) -> StageTypes.LinePlan:
    return StageTypes.LinePlan(
        line=line,
        description=f"invoice item {line}",
        quantity=Decimal(1),
        sure=sure,
        suggestion=ask[0] if ask else None,
        rivals=[],
        ask=ask,
    )


def typed_invoice(registry: type[EntitiesRegistry]) -> Any:
    extracted = registry.ExtractionStage.typed(reading("INV-028", po_reference_text="")).extraction
    assert extracted is not None
    return extracted


def test_only_not_sure_lines_get_questions_and_there_is_no_cap(registry: type[EntitiesRegistry]) -> None:
    many = [candidate("PO-1", number, 50) for number in range(1, 31)]
    plans = [plan(1, sure=True, ask=[candidate("PO-1", 1, 100)]), plan(2, sure=False, ask=many)]

    _, questions, _ = entity().request_lines(
        typed_invoice(registry), vendor_name="B", taxable=Decimal(1), pool=[], printed=[], plans=plans
    )

    assert len(questions) == 30
    assert all(name.startswith("line_2__PO-1__") for name in questions)


def test_state_holds_the_full_invoice_and_pos_but_no_bank_or_tax_id(registry: type[EntitiesRegistry]) -> None:
    used = snapshot("PO-1", 1, ("Monitor", "20", "20"), ("Headset", "5", "1"))
    state, _, key = entity().request_lines(
        typed_invoice(registry),
        vendor_name="Brightline",
        taxable=Decimal(199000),
        pool=[used],
        printed=[],
        plans=[plan(1, sure=False, ask=[candidate("PO-1", 2, 60)])],
    )
    text = json.dumps(state)

    assert state["invoice"]["invoice_number"] == "BLI/24-25/0431"
    assert [line["description"] for line in state["invoice"]["lines"]] == [
        "Wireless headset",
        "Height-adjustable office desk",
    ]
    assert state["purchase_orders"][0]["lines"][0]["nothing_left"] is True
    assert "bank" not in text
    assert "tax_id" not in text
    assert "29AAFCB7421K1Z3" not in text
    assert "left" not in json.dumps(key["state"]["purchase_orders"])
    assert "remaining" not in json.dumps(key["state"])


async def test_decide_reads_selected_dropped_and_unsure(registry: type[EntitiesRegistry]) -> None:
    plans = [
        plan(1, sure=False, ask=[candidate("PO-1", 1, 80), candidate("PO-2", 1, 75, day=2)]),
        plan(2, sure=False, ask=[candidate("PO-1", 2, 40)]),
        plan(3, sure=False, ask=[candidate("PO-1", 3, 60)]),
    ]
    answers = {"PO-1/1": 0.95, "PO-2/1": 0.91, "PO-1/2": 0.10, "PO-1/3": 0.65}
    fake = FakeJev()
    fake.answers = {}

    async def evaluate(state: Any, questions: Any, *, timeout: float) -> dict[str, float]:  # noqa: ASYNC109
        return {name: answers["/".join(name.split("__")[1:])] for name in questions}

    fake.evaluate = evaluate  # type: ignore[method-assign]
    decisions, record = await entity(fake).decide(
        typed_invoice(registry), vendor_name="B", taxable=Decimal(1), pool=[], printed=[], plans=plans
    )

    assert record.state == "answered" and record.asked == [1, 2, 3]
    assert decisions[1][0] == ["PO-1/1", "PO-2/1"]
    assert decisions[2][0] == []
    assert decisions[3][0] is None


async def test_a_foreign_or_missing_line_answer_skips_every_line(registry: type[EntitiesRegistry]) -> None:
    plans = [plan(1, sure=False, ask=[candidate("PO-1", 1, 80)])]
    fake = FakeJev()

    async def evaluate(state: Any, questions: Any, *, timeout: float) -> dict[str, float]:  # noqa: ASYNC109
        return {"line_9__PO-9__9": 0.9}

    fake.evaluate = evaluate  # type: ignore[method-assign]
    decisions, record = await entity(fake).decide(
        typed_invoice(registry), vendor_name="B", taxable=Decimal(1), pool=[], printed=[], plans=plans
    )

    assert decisions == {1: (None, {})}
    assert record.state == "skipped"


def allocated(*parts: tuple[int, str, str]) -> StageTypes.OrderMatch:
    """An OrderMatch whose allocations are (invoice line, PO, quantity) on line 1 of that PO."""
    return StageTypes.OrderMatch(
        po_number=parts[0][1],
        others=sorted({po for _, po, _ in parts} - {parts[0][1]}),
        method="jev",
        signals=[],
        score=100.0,
        needed=80.0,
        candidates=[],
        findings=[],
        allocations=[
            StageTypes.LineAllocation(
                line=line,
                description="Chair",
                po_number=po,
                po_line=1,
                quantity=Decimal(quantity),
                method="jev_changed",
                score=90.0,
                suggestion=None,
            )
            for line, po, quantity in parts
        ],
    )


def chairs(*quantities: str) -> list[StageTypes.PricedLine]:
    return [
        StageTypes.PricedLine(
            description="Chair", quantity=Decimal(q), unit_price=Decimal(1000), tax_rate_pct=Decimal(18)
        )
        for q in quantities
    ]


def test_a_split_line_is_checked_as_portions(registry: type[EntitiesRegistry]) -> None:
    pools = [snapshot("PO-1", 1, ("Chair", "50", "0")), snapshot("PO-2", 2, ("Chair", "50", "0"))]

    checked = registry.AmountsStage.run(
        allocated((1, "PO-1", "50"), (1, "PO-2", "50")),
        pools,
        Decimal(100000),
        reading=typed_invoice(registry),
        priced=chairs("100"),
        pct=Decimal(2),
        invoice_rate=Decimal(18),
    )

    assert checked.within
    assert [(part.po_number, part.invoice_amount) for part in checked.parts] == [
        ("PO-1", Decimal("50000.00")),
        ("PO-2", Decimal("50000.00")),
    ]
    assert all(line.ok for line in checked.lines)


def test_two_lines_on_one_po_line_are_checked_together(registry: type[EntitiesRegistry]) -> None:
    pools = [snapshot("PO-1", 1, ("Chair", "50", "0"))]

    checked = registry.AmountsStage.run(
        allocated((1, "PO-1", "30"), (2, "PO-1", "30")),
        pools,
        Decimal(60000),
        reading=typed_invoice(registry),
        priced=chairs("30", "30"),
        pct=Decimal(2),
        invoice_rate=Decimal(18),
    )

    assert "QTY_EXCEEDS_ORDERED" in [finding.code for finding in checked.findings]


def test_a_split_sees_what_earlier_lines_already_took(registry: type[EntitiesRegistry]) -> None:
    item = StageTypes.LinePlan(
        line=2, description="chairs", quantity=Decimal(5), sure=False, suggestion=None, rivals=[], ask=[]
    )
    first, second = candidate("A", 1, 90, left="10", day=1), candidate("B", 1, 90, left="10", day=2)

    portions = registry.PoStage.allocate(item, [first, second], "jev_changed", {}, {"A/1": Decimal(10)})

    assert [(p.po_number, p.quantity) for p in portions] == [("B", Decimal(5))]


def test_question_names_use_only_safe_characters() -> None:
    assert PoJevClass.line_question(3, "PO-2024-0159/4") == "line_3__PO-2024-0159__4"


def chosen_plan(*descriptions: str) -> StageTypes.LinePlan:
    """A not-sure line whose ask lists one PO line per description (PO-1, PO-2, … dated in that order)."""
    ask = [
        StageTypes.LineCandidate(
            po_number=f"PO-{index}",
            po_line=1,
            po_date=date(2024, 10, index),
            description=text,
            score=85.0,
            left=Decimal(50),
        )
        for index, text in enumerate(descriptions, start=1)
    ]
    return StageTypes.LinePlan(
        line=1, description="item", quantity=Decimal(1), sure=False, suggestion=ask[0], rivals=ask[1:], ask=ask
    )


def test_several_lines_of_the_same_item_fill_the_oldest_first(registry: type[EntitiesRegistry]) -> None:
    item = chosen_plan("Stackable visitor chair", "Stackable visitor chair")

    settled = registry.PoStage._settle(item, {1: (["PO-1/1", "PO-2/1"], {"PO-1/1": 0.95, "PO-2/1": 0.94})}, {})

    assert [(a.po_number, a.quantity, a.method) for a in settled] == [("PO-1", Decimal(1), "jev_kept")]


def test_several_lines_of_different_items_are_held(registry: type[EntitiesRegistry]) -> None:
    item = chosen_plan("Freight - Bengaluru to Mumbai (October)", "Freight - Bengaluru to Delhi (October)")

    settled = registry.PoStage._settle(item, {1: (["PO-1/1", "PO-2/1"], {"PO-1/1": 0.95, "PO-2/1": 0.95})}, {})

    assert [a.method for a in settled] == ["unmatched"]
    assert "different items" in registry.PoStage._why(settled[0], 0.50, 0.80)
