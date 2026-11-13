"""Manual invoice entry: a typed invoice goes through stages 2 to 8 with no rule relaxed."""

import json
from typing import TYPE_CHECKING, Any

import pytest
from conftest import DATA
from test_master import KESTREL, RACK, post
from test_pipeline import STEPS, stream

from app.entities import EntitiesRegistry

if TYPE_CHECKING:
    import httpx


def typed(**changes: object) -> dict[str, Any]:
    """Return INV-001's reading as a person would type it, without source quotes, with some fields replaced."""
    raw = json.loads((DATA / "invoices" / "INV-001.read.json").read_text())
    raw.pop("sources")
    return raw | changes


async def run(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    """Post a typed invoice, follow it to its done event and return its detail."""
    status, created = await post(client, "/v1/runs/manual", body)
    assert status == 202
    events = await stream(client, created["id"])
    assert events[-1][0] == "done"
    return (await client.get(f"/v1/runs/{created['id']}")).json()


def codes(detail: dict[str, Any]) -> set[str]:
    return {reason["code"] for reason in detail["reasons"]}


async def balance(client: httpx.AsyncClient, po: str) -> str:
    orders = {order["po"]: order for order in (await client.get("/v1/orders")).json()["data"]}
    return orders[po]["left"]


async def test_typed_invoice_runs_like_the_sample(client: httpx.AsyncClient) -> None:
    manual = await run(client, typed())
    left = await balance(client, "PO-2024-0142")
    again = await run(client, typed())

    await EntitiesRegistry.Pipeline.reset()
    sample_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    await stream(client, sample_id)
    sample = (await client.get(f"/v1/runs/{sample_id}")).json()

    assert (manual["outcome"], manual["vendor_id"], manual["po"], left) == (
        "approved",
        "V001",
        "PO-2024-0142",
        "24000.00",
    )
    assert codes(manual) == codes(sample)
    assert (manual["file"], manual["mode"], manual["tokens"], manual["number"]) == (
        None,
        "manual",
        None,
        "ACM/24-25/0187",
    )
    assert [step["name"] for step in manual["steps"]] == STEPS
    assert manual["steps"][0] == {
        "name": "intake",
        "state": "skipped",
        "ms": 0,
        "data": {"reason": "Entered manually; there is no file."},
        "findings": [],
    }
    assert manual["steps"][1]["state"] == "done"
    assert manual["steps"][1]["data"]["mode"] == "manual"
    assert manual["steps"][1]["data"]["usage"] is None
    assert manual["steps"][1]["data"]["extraction"]["subtotal"] == "36000.00"
    assert again["outcome"] == "rejected"
    assert "DUPLICATE_EXACT" in codes(again)


async def test_typed_invoice_keeps_every_block(client: httpx.AsyncClient) -> None:
    wrong_bank = await run(client, typed(bank_account_number="77700011122"))
    no_number = await run(client, typed(invoice_number=""))

    assert (wrong_bank["outcome"], no_number["outcome"]) == ("needs_attention", "needs_attention")
    assert "BANK_ACCOUNT_MISMATCH" in codes(wrong_bank)
    assert "MISSING_INVOICE_NUMBER" in codes(no_number)
    assert await balance(client, "PO-2024-0142") == "60000.00"


async def test_minimal_typed_invoice_is_accepted(client: httpx.AsyncClient) -> None:
    detail = await run(client, {})

    assert (detail["status"], detail["outcome"], detail["mode"]) == ("completed", "needs_attention", "manual")
    assert {"MISSING_INVOICE_NUMBER", "MISSING_TOTAL", "VENDOR_NOT_FOUND"} <= codes(detail)


@pytest.mark.parametrize(
    "body",
    [
        typed(sources=[]),
        typed(lines=[{"description": "x", "colour": "red"}]),
        typed(tax_lines=[{"label": "IGST", "note": ""}]),
        typed(lines=[{"description": "x"}] * 51),
        typed(tax_lines=[{"label": "IGST"}] * 11),
        typed(supplier_name="x" * 501),
        typed(lines=[{"description": "x" * 501}]),
    ],
)
async def test_body_limits_and_unknown_keys_are_refused(client: httpx.AsyncClient, body: dict[str, Any]) -> None:
    response = await client.post("/v1/runs/manual", json=body)

    assert response.status_code == 422
    assert (await client.get("/v1/runs")).json() == {"data": []}


async def test_created_master_data_is_used_by_the_pipeline(client: httpx.AsyncClient) -> None:
    _, vendor = await post(client, "/v1/vendors", KESTREL)
    _, order = await post(client, "/v1/orders", {"vendor_id": vendor["id"], "date": "2024-11-01", "lines": [RACK]})
    detail = await run(
        client,
        typed(
            supplier_name="Kestrel Fasteners Pvt Ltd",
            supplier_tax_id="29ABCDE1234F1Z5",
            bank_account_name="Kestrel Fasteners Pvt Ltd",
            bank_account_number="601234567890",
            bank_code="ICIC0001234",
            bank_name="ICICI Bank",
            invoice_number="KF/24-25/0001",
            po_reference_text=order["po"],
            lines=[
                {
                    "description": "Steel rack 4-tier",
                    "quantity": "10",
                    "unit": "pcs",
                    "unit_price": "2,450.00",
                    "line_amount": "24,500.00",
                    "tax_rate_pct": "18",
                }
            ],
            subtotal="24,500.00",
            tax_lines=[{"label": "IGST", "rate_pct": "18", "amount": "4,410.00"}],
            total="28,910.00",
        ),
    )

    assert (vendor["id"], order["po"], order["left"]) == ("V014", "PO-2024-0169", "61250.00")
    assert (detail["outcome"], detail["vendor_id"], detail["po"]) == ("approved", "V014", "PO-2024-0169")
    assert {"VENDOR_IDENTIFIED", "PO_MATCHED", "PARTIAL_DELIVERY", "NO_DUPLICATE"} <= codes(detail)
    assert await balance(client, "PO-2024-0169") == "36750.00"
