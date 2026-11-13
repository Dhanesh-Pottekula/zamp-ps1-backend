"""Regression tests for the defects found in review; each failed before its fix."""

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import anthropic
import httpx2
import pytest
from conftest import DATA, extraction, line
from pydantic import ValidationError
from test_order import match
from test_pipeline import stream
from test_schema import extract, live, response

from app.adapters import ExtractionError
from app.config.env_config import _LlmConfigClass
from app.entities import EntitiesRegistry, StageTypes
from app.entities.stages.extraction_stage import _Helper as ExtractionHelper
from app.entities.stages.po_stage import _Helper as OrderHelper

if TYPE_CHECKING:
    import httpx
    from conftest import StageRules

    from app.adapters import AdapterRegistry

TODAY = date(2026, 11, 12)


def codes(findings: list[Any]) -> list[str]:
    return [finding.code for finding in findings]


def test_credit_note_is_blocked_in_validation(rules: StageRules) -> None:
    credit = extraction(
        subtotal="(36,000.00)",
        tax_lines=[],
        total="(36,000.00)",
        lines=[line(quantity="(30)", line_amount="(36,000.00)")],
    )

    result = rules.validation.run(credit, TODAY)

    assert result.taxable_value == Decimal("-36000.00")
    assert [(f.code, f.severity) for f in result.findings if f.code == "NON_POSITIVE_AMOUNT"] == [
        ("NON_POSITIVE_AMOUNT", "block")
    ]


async def test_consume_refuses_non_positive_amounts(registry: type[EntitiesRegistry], adapter: AdapterRegistry) -> None:
    for amount, quantity in (("-36000", "30"), ("0", "0"), ("36000", "-30")):
        update = StageTypes.OrderUpdate(
            po_number="PO-2024-0142",
            amount=Decimal(amount),
            remaining_after=Decimal(0),
            quantities={1: Decimal(quantity)},
        )
        with pytest.raises(ValueError, match="consume"):
            await adapter.DB.run(lambda session, update=update: registry.Order.consume_in(session, update))

    order = await registry.Order.snapshot("PO-2024-0142")
    assert order is not None
    assert order.remaining == Decimal(60000)


@pytest.mark.parametrize("reference", ["PO-2024-01425", "REPO-2024-0142x", "Ref 2024-01425", "PO-2024-9999"])
async def test_malformed_or_unknown_po_numbers_are_not_found(registry: type[EntitiesRegistry], reference: str) -> None:
    lines = [
        StageTypes.PricedLine(
            description="Laptop stand", quantity=Decimal(30), unit_price=Decimal(1200), tax_rate_pct=Decimal(18)
        )
    ]
    result = await match(registry, reference, "V001", "36000", lines)

    assert result.po_number is None
    assert codes(result.findings) == ["PO_NOT_FOUND"]


async def test_reference_without_a_po_number_still_matches_by_lines(registry: type[EntitiesRegistry]) -> None:
    lines = [
        StageTypes.PricedLine(
            description="USB-C docking connector module",
            quantity=Decimal(300),
            unit_price=Decimal(150),
            tax_rate_pct=Decimal(18),
        )
    ]
    result = await match(registry, "as per your order dated 3 Oct", "V003", "45000", lines)

    assert (result.po_number, result.method) == ("PO-2024-0144", "lines")


async def test_follow_never_regresses_a_finished_step_to_running(registry: type[EntitiesRegistry]) -> None:
    run = registry.Run
    run_id = await run.start("INV-001.pdf", None, "recorded")
    await run.record(StageTypes.StepEvent(run_id=run_id, name="intake", state="done", millis=1, data={}, findings=[]))
    follower = run.follow(run_id)

    first = await anext(follower)
    for event in (
        StageTypes.StepEvent(run_id=run_id, name="intake", state="running", millis=0, data={}, findings=[]),
        StageTypes.StepEvent(run_id=run_id, name="intake", state="done", millis=1, data={}, findings=[]),
        StageTypes.StepEvent(run_id=run_id, name="extraction", state="running", millis=0, data={}, findings=[]),
        StageTypes.StepEvent(run_id=run_id, name="extraction", state="done", millis=1, data={}, findings=[]),
        StageTypes.RunClosed(status="completed", outcome="approved", summary="ok"),
    ):
        run.publish(event, run_id)

    rest = [item async for item in follower]
    seen = [(item.name, item.state) for item in [first, *rest] if isinstance(item, StageTypes.StepEvent)]
    assert seen == [("intake", "done"), ("extraction", "running"), ("extraction", "done")]
    assert isinstance(rest[-1], StageTypes.RunClosed)


async def test_name_only_vendor_blocks(registry: type[EntitiesRegistry]) -> None:
    party = extraction().supplier.model_copy(update={"tax_id": None})
    bank = extraction().supplier_bank.model_copy(update={"account_number": None})

    result = await registry.VendorStage.run(party, bank)

    assert [(f.code, f.severity) for f in result.findings] == [("VENDOR_NAME_ONLY", "block")]


def test_header_level_only_blocks(rules: StageRules) -> None:
    order = StageTypes.OrderSnapshot(
        po_number="PO-2024-0142",
        vendor_id="V001",
        vendor_name="Acme",
        po_date=date(2024, 10, 1),
        currency="INR",
        status="open",
        total=Decimal(60000),
        consumed=Decimal(0),
        remaining=Decimal(60000),
        lines=[],
    )

    result = rules.amounts.amount_check(
        order, Decimal(36000), currency="INR", lines=[], mapping=[], pct=Decimal(2), invoice_rate=None
    )

    assert ("HEADER_LEVEL_ONLY", "block") in [(f.code, f.severity) for f in result.findings]


async def test_approved_run_reads_as_a_full_note(client: httpx.AsyncClient) -> None:
    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    await stream(client, run_id)

    detail = (await client.get(f"/v1/runs/{run_id}")).json()

    assert [reason["code"] for reason in detail["reasons"]] == [
        "VENDOR_IDENTIFIED",
        "BANK_ACCOUNT_VERIFIED",
        "NO_DUPLICATE",
        "PO_MATCHED",
        "AMOUNT_WITHIN_TOLERANCE",
        "PARTIAL_DELIVERY",
    ]
    assert detail["reasons"][0]["text"] == "Vendor Acme Supplies Pvt Ltd identified by GSTIN 29AACCA1234F1Z5."
    assert detail["reasons"][1]["text"] == (
        "The invoice asks for payment to account **********5678, the account the vendor master holds for Acme "
        "Supplies Pvt Ltd."
    )
    assert detail["reasons"][3]["text"] == "Matched PO-2024-0142 by the reference printed on the invoice."


def test_within_tolerance_but_short_is_partial(rules: StageRules) -> None:
    order = StageTypes.OrderSnapshot(
        po_number="PO-2024-0142",
        vendor_id="V001",
        vendor_name="Acme",
        po_date=date(2024, 10, 1),
        currency="INR",
        status="open",
        total=Decimal(60000),
        consumed=Decimal(0),
        remaining=Decimal(60000),
        lines=[],
    )

    result = rules.amounts.amount_check(
        order, Decimal(59500), currency="INR", lines=[], mapping=[], pct=Decimal(2), invoice_rate=None
    )
    decision = rules.decision.run([f for f in result.findings if f.code != "HEADER_LEVEL_ONLY"], result, "PO-2024-0142")

    assert "PO_FULLY_INVOICED" not in codes(result.findings)
    assert "PARTIAL_DELIVERY" in codes(result.findings)
    assert "500.00" in decision.summary
    assert "fully invoiced" not in decision.summary


async def test_held_invoice_rerun_is_not_its_own_duplicate(client: httpx.AsyncClient) -> None:
    first = (await client.post("/v1/runs/samples/INV-019")).json()["id"]
    await stream(client, first)
    second = (await client.post("/v1/runs/samples/INV-019")).json()["id"]
    await stream(client, second)

    detail = (await client.get(f"/v1/runs/{second}")).json()

    assert detail["outcome"] == "needs_attention"
    assert "DUPLICATE_EXACT" not in [reason["code"] for reason in detail["reasons"]]
    assert "NO_DUPLICATE" in [reason["code"] for reason in detail["reasons"]]


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("₹(500.00)", Decimal("-500.00")),
        ("-Rs 500", Decimal(-500)),
        ("500.00-", Decimal("-500.00")),
        ("36.000,00", None),
        ("1.200,50", None),
        ("1.200.000", None),
    ],
)
def test_signs_anywhere_and_european_forms(printed: str, expected: Decimal | None) -> None:
    assert ExtractionHelper.amount(printed) == expected


@pytest.mark.parametrize("printed", ["20241104", "2024-W45-1", "2024-11-04T10:00"])
def test_iso_branch_is_strict(printed: str) -> None:
    assert ExtractionHelper.day(printed, "") is None


def test_zero_rate_line_is_respected(rules: StageRules) -> None:
    inclusive = extraction(prices_include_tax=True, lines=[line(tax_rate_pct="0", unit_price="1,200.00")])

    lines = rules.validation.priced_lines(inclusive, rules.validation.run(inclusive, TODAY))

    assert lines[0].unit_price == Decimal("1200.00")


def test_summary_leads_with_the_reason(rules: StageRules) -> None:
    findings = rules.validation.run(extraction(invoice_number=""), TODAY).findings

    decision = rules.decision.run(findings, None, None)

    assert decision.outcome == "needs_attention"
    assert decision.summary.startswith("No invoice number could be read")


async def test_server_errors_are_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr("app.adapters.llm_adp.asyncio.sleep", no_wait)
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    overloaded = anthropic.InternalServerError("overloaded", response=httpx2.Response(529, request=request), body=None)
    adapter, messages = live([overloaded, response()])

    parsed, _ = await extract(adapter)

    assert parsed == "parsed"
    assert len(messages.calls) == 2


async def test_schema_failure_explains_the_likely_stop_reason() -> None:
    try:
        StageTypes.RunClosed.model_validate({"status": "nope"})
    except ValidationError as error:
        invalid = error

    adapter, _ = live([invalid])

    with pytest.raises(ExtractionError, match="max_tokens"):
        await extract(adapter)


def test_max_tokens_is_capped_for_non_streaming_calls() -> None:
    with pytest.raises(ValidationError):
        _LlmConfigClass(max_tokens=30000)


@pytest.mark.parametrize(
    ("filename", "stored"),
    [
        (None, None),
        ("", None),
        (".", None),
        ("..", None),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("dir\\INV-001.pdf", "INV-001.pdf"),
    ],
)
async def test_odd_upload_names_do_not_crash_intake(
    registry: type[EntitiesRegistry], filename: str | None, stored: str | None
) -> None:
    pdf = (DATA / "invoices" / "INV-001.pdf").read_bytes()

    run_id = await registry.Pipeline.submit(filename, pdf, None)
    items = [item async for item in registry.Run.follow(run_id)]
    detail = await registry.Run.detail(run_id)

    assert isinstance(items[-1], StageTypes.RunClosed)
    assert items[-1].status == "completed"
    assert detail is not None
    assert detail.filename == stored


async def test_failing_failure_handler_still_closes_the_stream(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(*_: object, **__: object) -> None:
        message = "database gone"
        raise RuntimeError(message)

    monkeypatch.setattr(EntitiesRegistry.VendorStage, "run", explode)
    monkeypatch.setattr(EntitiesRegistry.Run, "close", explode)

    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    events = await stream(client, run_id)

    assert events[-1][0] == "done"
    assert events[-1][1]["status"] == "error"


def test_mistyped_reference_beats_a_po_in_a_description() -> None:
    assert OrderHelper.reference("PO-2024-01425", ["Laptop stands per PO-2024-0142"]) is None
    assert OrderHelper.reference("", ["Laptop stands per PO-2024-0142"]) == "PO-2024-0142"
    assert OrderHelper.reference("as per your order dated 3 Oct", ["per PO-2024-0142"]) == "PO-2024-0142"
