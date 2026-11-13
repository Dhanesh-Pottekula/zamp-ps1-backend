"""End to end: the golden scenario through the pipeline, and the HTTP API including the SSE stream."""

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from conftest import DATA

from app.entities import EntitiesRegistry, StageTypes
from scripts.run_golden import GoldenRunner

if TYPE_CHECKING:
    import httpx

    from app.adapters import AdapterRegistry

RUN_KEYS = {
    "id",
    "file",
    "sample",
    "status",
    "outcome",
    "vendor_id",
    "vendor",
    "po",
    "invoice_id",
    "number",
    "amount",
    "currency",
    "summary",
    "tokens",
    "mode",
    "at",
    "ms",
}
STEP_KEYS = {"name", "state", "ms", "data", "findings"}
FINDING_KEYS = {"code", "level", "text", "data"}
REFERENCED = ("vendor_id", "vendor", "po", "invoice_id", "number", "amount")
STEPS = ["intake", "extraction", "validation", "vendor", "bank", "duplicate", "po", "amounts", "decision", "persist"]


async def test_golden_scenario_in_recorded_mode(registry: type[EntitiesRegistry]) -> None:
    results = await GoldenRunner.evaluate()

    assert [expected.id for expected, _, _ in results] == [f"INV-{number:03d}" for number in range(1, 47)]
    assert {expected.id: problems for expected, _, problems in results if problems} == {}


async def test_close_amount_embedded_tax_and_missing_number(client: httpx.AsyncClient) -> None:
    details: dict[str, dict[str, Any]] = {}
    for sample in ("INV-041", "INV-009", "INV-004"):
        run_id = (await client.post(f"/v1/runs/samples/{sample}")).json()["id"]
        await stream(client, run_id)
        details[sample] = (await client.get(f"/v1/runs/{run_id}")).json()

    reasons = {sample: {reason["code"]: reason for reason in detail["reasons"]} for sample, detail in details.items()}
    orders = {order["po"]: order for order in (await client.get("/v1/orders")).json()["data"]}

    assert [detail["outcome"] for detail in details.values()] == ["approved", "approved", "needs_attention"]
    assert not {"UNIT_PRICE_MISMATCH", "QTY_EXCEEDS_ORDERED", "HEADER_LEVEL_ONLY", "DUPLICATE_SUSPECTED"} & set(
        reasons["INV-041"]
    )
    assert reasons["INV-041"]["AMOUNT_WITHIN_TOLERANCE"]["text"] == (
        "The taxable value 99,000.00 exceeds the 98,000.00 left on PO-2024-0151 by 1,000.00, within the 1,960.00 "
        "tolerance."
    )
    assert details["INV-041"]["summary"] == (
        "Approved; PO-2024-0151 is now fully invoiced, 1,000.00 over its balance and within the 1,960.00 tolerance."
    )
    assert details["INV-009"]["summary"] == "Approved; PO-2024-0152 is now fully invoiced."
    assert reasons["INV-041"]["PO_FULLY_INVOICED"]["data"]["remaining_after"] == "-1000.00"
    assert [orders["PO-2024-0151"][key] for key in ("status", "used", "left")] == [
        "fully_invoiced",
        "99000.00",
        "-1000.00",
    ]
    assert not {"TAX_RATE_MISMATCH", "UNIT_PRICE_MISMATCH"} & set(reasons["INV-009"])
    assert "TAXABLE_VALUE_DERIVED" in reasons["INV-009"]
    assert details["INV-009"]["amount"] == "50000.00"
    assert (orders["PO-2024-0152"]["status"], orders["PO-2024-0152"]["left"]) == ("fully_invoiced", "0.00")
    assert reasons["INV-004"]["MISSING_INVOICE_NUMBER"]["level"] == "block"
    assert details["INV-004"]["number"] is None
    assert isinstance(details["INV-004"]["invoice_id"], int)
    assert details["INV-004"]["amount"] == "45000.00"
    assert (orders["PO-2024-0153"]["status"], orders["PO-2024-0153"]["left"]) == ("open", "45000.00")


def stored(sql: str) -> list[tuple[Any, ...]]:
    """Run one read-only statement on the test database file."""
    with closing(sqlite3.connect(os.environ["STORAGE_DB_PATH"])) as connection:
        return connection.execute(sql).fetchall()


async def stream(client: httpx.AsyncClient, run_id: str) -> list[tuple[str, dict[str, Any]]]:
    """Read a run's SSE stream to its done event."""
    events: list[tuple[str, dict[str, Any]]] = []
    async with asyncio.timeout(10), client.stream("GET", f"/v1/runs/{run_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        name = ""
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((name, json.loads(line.removeprefix("data: "))))

    return events


async def test_sample_run_streams_to_done(client: httpx.AsyncClient) -> None:
    created = await client.post("/v1/runs/samples/INV-001")
    run_id = created.json()["id"]

    events = await stream(client, run_id)
    detail = (await client.get(f"/v1/runs/{run_id}")).json()

    assert created.status_code == 202
    assert set(created.json()) == {"id"}
    assert events[-1] == (
        "done",
        {
            "status": "completed",
            "outcome": "approved",
            "summary": "Approved as partial delivery; 24,000.00 remains open on PO-2024-0142.",
        },
    )
    finals = [data for name, data in events if name == "step" and data["state"] != "running"]
    assert [step["name"] for step in finals] == STEPS
    assert all(set(step) == STEP_KEYS for name, step in events if name == "step")
    assert finals == detail["steps"]
    assert set(detail) == RUN_KEYS | {"reasons", "steps"}
    assert all(set(reason) == FINDING_KEYS for reason in detail["reasons"])
    assert (detail["vendor_id"], detail["vendor"], detail["po"], detail["number"], detail["amount"]) == (
        "V001",
        "Acme Supplies Pvt Ltd",
        "PO-2024-0142",
        "ACM/24-25/0187",
        "36000.00",
    )
    assert isinstance(detail["invoice_id"], int)
    assert detail["tokens"] == {"in": 0, "read": 0, "write": 0, "out": 0}

    replay = await stream(client, run_id)
    assert replay == [("step", step) for step in finals] + [events[-1]]


async def test_history_orders_and_samples(client: httpx.AsyncClient) -> None:
    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    await stream(client, run_id)

    runs = (await client.get("/v1/runs")).json()["data"]
    approved = (await client.get("/v1/runs", params={"outcome": "approved"})).json()["data"]
    rejected = (await client.get("/v1/runs", params={"outcome": "rejected"})).json()["data"]
    orders = {order["po"]: order for order in (await client.get("/v1/orders")).json()["data"]}
    samples = (await client.get("/v1/admin/samples")).json()["data"]
    meta = (await client.get("/v1/admin/meta")).json()

    assert [run["id"] for run in runs] == [run_id]
    assert set(runs[0]) == RUN_KEYS
    assert [run["id"] for run in approved] == [run_id]
    assert rejected == []
    assert set(orders["PO-2024-0142"]) == {"po", "vendor", "currency", "status", "total", "used", "left", "lines"}
    assert (orders["PO-2024-0142"]["used"], orders["PO-2024-0142"]["left"]) == ("36000.00", "24000.00")
    assert orders["PO-2024-0142"]["status"] == "partially_invoiced"
    assert orders["PO-2024-0142"]["lines"] == [
        {
            "no": 1,
            "text": "Laptop stand (adjustable aluminium)",
            "qty": "50",
            "used": "30",
            "price": "1200.00",
            "tax": "18",
        }
    ]
    assert set(samples[0]) == {"step", "id", "title", "expect", "outcome", "ran", "result"}
    assert (samples[0]["ran"], samples[0]["result"]) == (True, "approved")
    assert (samples[2]["ran"], samples[2]["result"]) == (False, None)
    assert meta == {"mode": "recorded", "model": "claude-opus-5-5"}


async def test_reset_is_rate_limited(client: httpx.AsyncClient) -> None:
    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    await stream(client, run_id)

    first = await client.post("/v1/admin/reset")
    second = await client.post("/v1/admin/reset")
    orders = {order["po"]: order for order in (await client.get("/v1/orders")).json()["data"]}

    assert (first.status_code, first.json()) == (200, {"ok": True})
    assert second.status_code == 429
    assert (await client.get("/v1/runs")).json() == {"data": []}
    assert orders["PO-2024-0142"]["left"] == "60000.00"
    assert orders["PO-2024-0148"]["left"] == "0.00"
    assert stored("SELECT count(*) FROM invoices") == [(0,)]

    again = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    await stream(client, again)
    row = (await client.get("/v1/runs")).json()["data"][0]

    assert (row["id"], row["vendor"], row["number"], row["amount"]) == (
        again,
        "Acme Supplies Pvt Ltd",
        "ACM/24-25/0187",
        "36000.00",
    )


async def test_unknown_run_is_not_found(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/runs/0123456789abcdef0123456789abcdef")).status_code == 404
    assert (await client.get("/v1/runs/nope/events")).status_code == 404


async def test_upload_runs_the_pipeline(client: httpx.AsyncClient) -> None:
    pdf = (DATA / "invoices" / "INV-002.pdf").read_bytes()

    created = await client.post("/v1/runs", files={"file": ("INV-002.pdf", pdf, "application/pdf")})
    events = await stream(client, created.json()["id"])
    intake = next(data for name, data in events if name == "step" and data["name"] == "intake")

    assert created.status_code == 202
    assert events[-1][1]["outcome"] == "approved"
    assert intake["data"]["text_layer"] is False


@pytest.mark.parametrize(
    ("filename", "content"),
    [("notes.txt", b"just some text"), ("broken.pdf", b"%PDF-1.4 truncated nonsense")],
)
async def test_unusable_files_are_rejected_at_intake(client: httpx.AsyncClient, filename: str, content: bytes) -> None:
    created = await client.post("/v1/runs", files={"file": (filename, content, "application/octet-stream")})
    events = await stream(client, created.json()["id"])
    finals = {data["name"]: data for name, data in events if name == "step" and data["state"] != "running"}

    assert events[-1][1]["outcome"] == "rejected"
    assert [finding["code"] for finding in finals["intake"]["findings"]] == ["FILE_REJECTED"]
    assert all(finals[name]["state"] == "skipped" for name in STEPS[1:7])
    assert finals["decision"]["state"] == "done"
    assert all(
        row[key] is None for row in (await client.get("/v1/runs")).json()["data"] for key in (*REFERENCED, "tokens")
    )


async def test_outcome_filter_accepts_an_empty_value(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/runs", params={"outcome": ""})

    assert (response.status_code, response.json()) == (200, {"data": []})


async def test_reasons_are_derived_from_the_steps(client: httpx.AsyncClient) -> None:
    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    await stream(client, run_id)

    detail = (await client.get(f"/v1/runs/{run_id}")).json()
    decision = next(step for step in detail["steps"] if step["name"] == "decision")

    expected = [
        "VENDOR_IDENTIFIED",
        "BANK_ACCOUNT_VERIFIED",
        "NO_DUPLICATE",
        "PO_MATCHED",
        "AMOUNT_WITHIN_TOLERANCE",
        "PARTIAL_DELIVERY",
    ]
    assert set(decision["data"]) == {"gates", "others", "update"}
    assert [(gate["name"], gate["passed"]) for gate in decision["data"]["gates"]] == [
        ("vendor", True),
        ("bank", True),
        ("po", True),
        ("amount", True),
    ]
    assert decision["findings"] == []
    assert [reason["code"] for reason in detail["reasons"]] == expected


async def test_derived_reasons_match_the_decision(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    decide = EntitiesRegistry.DecisionStage.run
    decided: list[StageTypes.Decision] = []

    def capture(*args: Any, **named: Any) -> StageTypes.Decision:
        decision = decide(*args, **named)
        decided.append(decision)
        return decision

    monkeypatch.setattr(EntitiesRegistry.DecisionStage, "run", capture)

    details: dict[str, dict[str, Any]] = {}
    for sample in ("INV-001", "INV-024", "INV-025", "INV-021"):
        run_id = (await client.post(f"/v1/runs/samples/{sample}")).json()["id"]
        await stream(client, run_id)
        details[sample] = (await client.get(f"/v1/runs/{run_id}")).json()

    expected = dict(zip(details, decided, strict=True))
    for sample in ("INV-001", "INV-025", "INV-021"):
        wire = [
            {"code": reason.code, "level": reason.severity, "text": reason.message, "data": reason.data}
            for reason in expected[sample].reasons
        ]
        assert details[sample]["reasons"] == wire

    assert [details[sample]["outcome"] for sample in ("INV-025", "INV-021")] == ["needs_attention", "rejected"]
    assert all("reasons" not in json.loads(data) for (data,) in stored("SELECT data FROM steps"))
    assert [set(json.loads(data)) for (data,) in stored("SELECT data FROM steps WHERE name = 'decision'")] == [
        {"gates", "others", "update"}
    ] * 4
    assert "reasons" not in {name for _, name, *_ in stored("PRAGMA table_info(runs)")}


async def test_runs_store_references_only(client: httpx.AsyncClient) -> None:
    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    await stream(client, run_id)

    columns = {name for _, name, *_ in stored("PRAGMA table_info(runs)")}
    (reference,) = stored("SELECT vendor_id, po_number, invoice_id FROM runs")
    (register,) = stored("SELECT id, run_id FROM invoices")
    (row,) = (await client.get("/v1/runs")).json()["data"]
    (usage,) = stored("SELECT json_extract(data, '$.usage') FROM steps WHERE name = 'extraction'")
    tokens = {"in": "input", "read": "cache_read", "write": "cache_write", "out": "output"}

    assert {"vendor_id", "po_number", "invoice_id"} <= columns
    assert not {"vendor_name", "invoice_number", "amount", "reasons", "usage"} & columns
    assert reference == ("V001", "PO-2024-0142", register[0])
    assert register[1] == run_id
    assert row["tokens"] == {short: json.loads(usage[0])[name] for short, name in tokens.items()}
    assert (row["vendor_id"], row["vendor"], row["po"], row["invoice_id"], row["number"], row["amount"]) == (
        "V001",
        "Acme Supplies Pvt Ltd",
        "PO-2024-0142",
        register[0],
        "ACM/24-25/0187",
        "36000.00",
    )


async def test_a_crashing_stage_ends_the_run_as_an_error(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(*_: object) -> None:
        message = "vendor master unavailable"
        raise RuntimeError(message)

    monkeypatch.setattr(EntitiesRegistry.VendorStage, "run", explode)

    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    events = await stream(client, run_id)
    detail = (await client.get(f"/v1/runs/{run_id}")).json()
    orders = {order["po"]: order for order in (await client.get("/v1/orders")).json()["data"]}

    assert events[-1] == (
        "done",
        {
            "status": "error",
            "outcome": None,
            "summary": "The run failed unexpectedly at the vendor stage; nothing was approved.",
        },
    )
    assert detail["status"] == "error"
    assert [(step["name"], step["state"]) for step in detail["steps"]][-1] == ("vendor", "failed")
    assert "RuntimeError" in detail["steps"][-1]["data"]["error"]
    assert orders["PO-2024-0142"]["left"] == "60000.00"
    assert all(detail[key] is None for key in REFERENCED)


async def test_a_crashed_run_keeps_the_findings_gathered_before_it(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(*_: object) -> None:
        message = "register unavailable"
        raise RuntimeError(message)

    monkeypatch.setattr(EntitiesRegistry.DuplicateStage, "run", explode)

    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    await stream(client, run_id)
    detail = (await client.get(f"/v1/runs/{run_id}")).json()

    assert detail["status"] == "error"
    # The duplicate check runs before PO matching, so a crash there keeps the vendor and bank findings only.
    assert [reason["code"] for reason in detail["reasons"]] == ["VENDOR_IDENTIFIED", "BANK_ACCOUNT_VERIFIED"]
    assert (detail["vendor_id"], detail["vendor"], detail["po"]) == ("V001", "Acme Supplies Pvt Ltd", None)
    assert (detail["invoice_id"], detail["number"], detail["amount"]) == (None, None, None)


async def test_unrecorded_document_needs_attention(client: httpx.AsyncClient) -> None:
    pdf = (DATA / "invoices" / "INV-001.pdf").read_bytes() + b"\n% one byte more than the sample\n"

    created = await client.post("/v1/runs", files={"file": ("INV-001.pdf", pdf, "application/pdf")})
    events = await stream(client, created.json()["id"])
    detail = (await client.get(f"/v1/runs/{created.json()['id']}")).json()

    assert events[-1][1]["outcome"] == "needs_attention"
    assert [reason["code"] for reason in detail["reasons"]] == ["EXTRACTION_FAILED"]
    assert detail["reasons"][0]["text"] == (
        "The invoice could not be read: no recorded reading matches this document; set ANTHROPIC_API_KEY or "
        "OPENAI_API_KEY to read new documents, or enter the invoice by hand."
    )
    assert all(detail[key] is None for key in REFERENCED)
    assert [step["state"] for step in detail["steps"]][2:7] == ["skipped"] * 5
    assert detail["tokens"] is None


async def test_history_resolves_what_each_run_read(client: httpx.AsyncClient) -> None:
    for number in range(1, 21):
        run_id = (await client.post(f"/v1/runs/samples/INV-{number:03d}")).json()["id"]
        await stream(client, run_id)

    runs = (await client.get("/v1/runs")).json()["data"]
    for run in runs:
        steps = {step["name"]: step["data"] for step in (await client.get(f"/v1/runs/{run['id']}")).json()["steps"]}
        if not steps["intake"]["accepted"]:  # a refused file (the password-protected sample) was never read
            assert (run["vendor"], run["po"], run["number"], run["amount"]) == (None, None, None, None)
            continue

        taxable = steps["validation"]["taxable_value"]
        assert run["vendor"] == steps["vendor"]["legal_name"]
        assert run["po"] == steps["po"]["po_number"]
        assert run["number"] == steps["extraction"]["extraction"]["invoice_number"]
        assert run["amount"] == (f"{Decimal(taxable):.2f}" if taxable is not None else None)

    assert len(runs) == 20


async def test_usage_is_read_from_the_extraction_step(registry: type[EntitiesRegistry]) -> None:
    counted = await registry.Run.start("counted.pdf", None, "live")
    skipped = await registry.Run.start("skipped.pdf", None, "live")
    usage = {"input": 1200, "cache_read": 900, "cache_write": 0, "output": 400}
    for run_id, data in ((counted, {"usage": usage}), (skipped, {"reason": "The file was rejected at intake."})):
        await registry.Run.record(
            StageTypes.StepEvent(run_id=run_id, name="extraction", state="done", millis=1, data=data, findings=[])
        )

    usages = await registry.Run.usages([counted, skipped])
    detail = await registry.Run.detail(counted)

    assert {run_id: found.model_dump() for run_id, found in usages.items()} == {counted: usage}
    assert detail is not None
    assert detail.usage is not None
    assert detail.usage.model_dump() == usage


async def test_a_run_serves_the_document_it_was_started_from(client: httpx.AsyncClient) -> None:
    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    await stream(client, run_id)

    shown = await client.get(f"/v1/runs/{run_id}/document")
    sample = await client.get("/v1/admin/samples/INV-001/document")

    assert (shown.status_code, shown.headers["content-type"]) == (200, "application/pdf")
    assert "content-disposition" not in shown.headers
    assert shown.content == sample.content == (DATA / "invoices" / "INV-001.pdf").read_bytes()
    assert sample.headers["content-disposition"] == 'inline; filename="INV-001.pdf"'


async def test_runs_without_a_stored_file_have_no_document(client: httpx.AsyncClient) -> None:
    rejected = (await client.post("/v1/runs", files={"file": ("notes.txt", b"just text", "text/plain")})).json()["id"]
    await stream(client, rejected)

    statuses = [
        (await client.get(f"/v1/runs/{run_id}/document")).status_code
        for run_id in (rejected, "0123456789abcdef", "..", "not-a-run")
    ]
    unknown = await client.get("/v1/admin/samples/INV-999/document")

    assert statuses == [404, 404, 404, 404]
    assert unknown.status_code == 404


async def test_the_store_never_looks_outside_the_upload_folder(adapter: AdapterRegistry) -> None:
    saved = await adapter.Store.save("abc123", "application/pdf", b"%PDF-1.7")

    assert await adapter.Store.find("abc123") == (saved, "application/pdf")
    for folder in ("..", "../abc123", "abc123/..", ".", "", "abc 123"):
        assert await adapter.Store.find(folder) is None


async def test_an_invoice_for_two_pos_consumes_both_or_neither(client: httpx.AsyncClient) -> None:
    for sample in ("INV-026", "INV-028"):
        run_id = (await client.post(f"/v1/runs/samples/{sample}")).json()["id"]
        await stream(client, run_id)

    detail = (await client.get(f"/v1/runs/{run_id}")).json()
    steps = {step["name"]: step["data"] for step in detail["steps"]}
    orders = {order["po"]: order for order in (await client.get("/v1/orders")).json()["data"]}

    assert (detail["outcome"], detail["po"]) == ("approved", "PO-2024-0159")
    assert detail["summary"] == (
        "Approved and split between 2 POs: 64,000.00 on PO-2024-0159 (220,000.00 remains open) and 135,000.00 on "
        "PO-2024-0160 (55,000.00 remains open)."
    )
    assert (steps["po"]["po_number"], steps["po"]["others"]) == ("PO-2024-0159", ["PO-2024-0160"])
    assert [(part["po_number"], part["invoice_amount"]) for part in steps["amounts"]["parts"]] == [
        ("PO-2024-0159", "64000.00"),
        ("PO-2024-0160", "135000.00"),
    ]
    assert [(line["po_number"], line["po_line"], line["ok"]) for line in steps["amounts"]["lines"]] == [
        ("PO-2024-0159", 4, True),
        ("PO-2024-0160", 1, True),
    ]
    assert steps["persist"]["others"] == [{"po_updated": "PO-2024-0160", "remaining_after": "55000.00"}]
    assert (orders["PO-2024-0159"]["left"], orders["PO-2024-0160"]["left"]) == ("220000.00", "55000.00")
    assert [line["used"] for line in orders["PO-2024-0160"]["lines"]] == ["15", "0"]
