"""Master data over HTTP: samples served from their table, vendor and PO creation, and reset back to the seed."""

import json
import os
import sqlite3
from contextlib import closing
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest
from conftest import DATA
from test_pipeline import stored, stream

from app.entities import EntitiesRegistry

if TYPE_CHECKING:
    import httpx

VENDOR_KEYS = {"id", "name", "country", "tax_id", "account", "bank_code", "bank", "currency", "status", "tolerance"}
KESTREL = {
    "name": "Kestrel Fasteners Pvt Ltd",
    "tax_id": "29 abcde 1234 f1z5",
    "account": "6012 3456 7890",
    "bank_code": "icic0001234",
    "bank": "ICICI Bank",
    "tolerance": 2.5,
}
RACK = {"text": "Steel rack 4-tier", "qty": 25, "price": "2450.00"}


def execute(sql: str) -> None:
    """Run one writing statement on the test database file and commit it."""
    with closing(sqlite3.connect(os.environ["STORAGE_DB_PATH"])) as connection:
        connection.execute(sql)
        connection.commit()


async def post(client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> tuple[int, Any]:
    response = await client.post(path, json=body)
    return response.status_code, response.json()


async def test_samples_are_seeded_into_the_table_and_served_from_it(client: httpx.AsyncClient) -> None:
    seed = json.loads((DATA / "samples.json").read_text())
    execute("UPDATE samples SET title = 'Edited in the table' WHERE id = 'INV-001'")
    samples = (await client.get("/v1/admin/samples")).json()["data"]

    assert stored("SELECT id, step, outcome FROM samples ORDER BY step") == [
        (row["id"], row["step"], row["outcome"]) for row in seed
    ]
    assert [sample["id"] for sample in samples] == [row["id"] for row in seed]
    assert samples[0]["title"] == "Edited in the table"


async def test_startup_seeds_an_empty_samples_table_without_a_reset(client: httpx.AsyncClient) -> None:
    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    await stream(client, run_id)
    execute("DELETE FROM samples")

    await EntitiesRegistry.Seed.initialize()

    assert stored("SELECT count(*) FROM samples") == [(33,)]
    assert stored("SELECT id FROM runs") == [(run_id,)]


async def test_sample_route_reads_the_table(client: httpx.AsyncClient) -> None:
    execute("DELETE FROM samples WHERE id = 'INV-002'")

    removed = await client.post("/v1/runs/samples/INV-002")

    assert (removed.status_code, removed.json()) == (404, {"detail": "Unknown sample INV-002"})


async def test_reset_restores_samples_vendors_and_orders(client: httpx.AsyncClient) -> None:
    vendor_status, vendor = await post(client, "/v1/vendors", KESTREL)
    order_status, _ = await post(client, "/v1/orders", {"vendor_id": vendor["id"], "lines": [RACK]})
    execute("DELETE FROM samples WHERE step > 5")
    execute("UPDATE samples SET title = 'Changed' WHERE id = 'INV-001'")

    reset = await client.post("/v1/admin/reset")
    vendors = (await client.get("/v1/vendors")).json()["data"]
    orders = (await client.get("/v1/orders")).json()["data"]
    samples = (await client.get("/v1/admin/samples")).json()["data"]
    seed = json.loads((DATA / "samples.json").read_text())

    assert (vendor_status, order_status, reset.status_code) == (201, 201, 200)
    assert [row["id"] for row in vendors] == [f"V{number:03d}" for number in range(1, 14)]
    assert [order["po"] for order in orders] == [f"PO-2024-{number:04d}" for number in range(142, 169)]
    assert [{key: sample[key] for key in seed[0]} for sample in samples] == seed


async def test_vendors_are_listed_by_id(client: httpx.AsyncClient) -> None:
    vendors = (await client.get("/v1/vendors")).json()["data"]

    assert [vendor["id"] for vendor in vendors] == [f"V{number:03d}" for number in range(1, 14)]
    assert set(vendors[0]) == VENDOR_KEYS
    assert vendors[0] == {
        "id": "V001",
        "name": "Acme Supplies Pvt Ltd",
        "country": "IN",
        "tax_id": "29AACCA1234F1Z5",
        "account": "50200012345678",
        "bank_code": "HDFC0001234",
        "bank": "HDFC Bank",
        "currency": "INR",
        "status": "approved",
        "tolerance": None,
    }
    assert vendors[5]["status"] == "suspended"


async def test_vendor_is_created_with_the_next_id(client: httpx.AsyncClient) -> None:
    status, vendor = await post(client, "/v1/vendors", KESTREL)
    us_status, us_vendor = await post(
        client,
        "/v1/vendors",
        {
            "name": "Harbor Tools Inc",
            "country": "us",
            "tax_id": "84-7654321",
            "account": "000987654321",
            "bank_code": "021000021",
            "bank": "Chase",
            "currency": "usd",
        },
    )
    listed = (await client.get("/v1/vendors")).json()["data"]

    assert status == 201
    assert vendor == {
        "id": "V014",
        "name": "Kestrel Fasteners Pvt Ltd",
        "country": "IN",
        "tax_id": "29ABCDE1234F1Z5",
        "account": "6012 3456 7890",
        "bank_code": "ICIC0001234",
        "bank": "ICICI Bank",
        "currency": "INR",
        "status": "approved",
        "tolerance": "2.5",
    }
    assert (us_status, us_vendor["id"], us_vendor["tax_id"], us_vendor["currency"]) == (
        201,
        "V015",
        "84-7654321",
        "USD",
    )
    assert us_vendor["tolerance"] is None
    assert stored("SELECT vendor_id, pan, email_domain, payment_terms FROM vendors WHERE vendor_id >= 'V014'") == [
        ("V014", "ABCDE1234F", "", ""),
        ("V015", "", "", ""),
    ]
    assert listed[-1] == us_vendor


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"name": "   "}, "The vendor name must not be blank."),
        (
            {"tax_id": "29AACCA1234F1Z"},
            "The tax ID 29AACCA1234F1Z is not a well-formed GSTIN, which an Indian vendor must have.",
        ),
        ({"tax_id": " - "}, "The tax ID must not be blank for an Indian vendor."),
        ({"account": ""}, "The bank account number must not be blank."),
        ({"bank_code": " "}, "The bank code must not be blank unless the account is an IBAN."),
        ({"bank": ""}, "The bank name must not be blank."),
        ({"country": "IND"}, "The country must be a two-letter ISO 3166 code, such as IN."),
        ({"currency": "RS"}, "The currency must be a three-letter ISO 4217 code, such as INR."),
        ({"tolerance": 0}, "The tolerance must be more than 0 and at most 20 percent."),
        ({"tolerance": 20.5}, "The tolerance must be more than 0 and at most 20 percent."),
    ],
)
async def test_invalid_vendor_is_refused(client: httpx.AsyncClient, changes: dict[str, Any], detail: str) -> None:
    status, body = await post(client, "/v1/vendors", KESTREL | changes)

    assert (status, body) == (422, {"detail": detail})
    assert stored("SELECT count(*) FROM vendors") == [(13,)]


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        (
            {"tax_id": "29aacca1234f1z5"},
            "The tax ID 29AACCA1234F1Z5 already belongs to Acme Supplies Pvt Ltd (V001).",
        ),
        (
            {"account": "5020-0012-3456-78", "bank_code": "hdfc0001234"},
            "The bank account **********5678 already belongs to Acme Supplies Pvt Ltd (V001).",
        ),
    ],
)
async def test_taken_identity_is_a_conflict(client: httpx.AsyncClient, changes: dict[str, Any], detail: str) -> None:
    status, body = await post(client, "/v1/vendors", KESTREL | changes)

    assert (status, body) == (409, {"detail": detail})


async def test_order_is_created_with_the_next_number(client: httpx.AsyncClient) -> None:
    status, order = await post(
        client,
        "/v1/orders",
        {
            "vendor_id": "V001",
            "date": "2025-03-01",
            "lines": [RACK, {"text": "  Shelf divider ", "qty": "10", "unit": "set", "price": 99.5, "tax": 12}],
        },
    )
    untaxed_status, untaxed = await post(client, "/v1/orders", {"vendor_id": "V007", "lines": [RACK]})
    usd_status, usd = await post(
        client, "/v1/orders", {"vendor_id": "V007", "date": "2023-06-30", "lines": [RACK | {"tax": 0}]}
    )
    listed = {row["po"]: row for row in (await client.get("/v1/orders")).json()["data"]}

    assert status == 201
    assert order == {
        "po": "PO-2025-0169",
        "vendor": "Acme Supplies Pvt Ltd",
        "currency": "INR",
        "status": "open",
        "total": "62245.00",
        "used": "0.00",
        "left": "62245.00",
        "lines": [
            {"no": 1, "text": "Steel rack 4-tier", "qty": "25", "used": "0", "price": "2450.00", "tax": "18"},
            {"no": 2, "text": "Shelf divider", "qty": "10", "used": "0", "price": "99.50", "tax": "12"},
        ],
    }
    assert (untaxed_status, untaxed) == (
        422,
        {"detail": "Line 1 needs a tax rate for a US vendor; enter 0 when no tax applies."},
    )
    assert (usd_status, usd["po"], usd["currency"]) == (201, "PO-2023-0170", "USD")
    assert listed["PO-2025-0169"] == order
    assert stored("SELECT unit, tax_rate_pct FROM order_lines WHERE po_number = 'PO-2025-0169' ORDER BY line_no") == [
        ("pcs", "18"),
        ("set", "12"),
    ]
    assert stored("SELECT po_date FROM orders WHERE po_number = 'PO-2023-0170'") == [("2023-06-30",)]


async def test_order_date_defaults_to_today(client: httpx.AsyncClient) -> None:
    status, order = await post(client, "/v1/orders", {"vendor_id": "V002", "lines": [RACK]})

    assert (status, order["po"]) == (201, f"PO-{date.today().year}-0169")


@pytest.mark.parametrize(
    ("body", "code", "detail"),
    [
        ({"vendor_id": "V999", "lines": [RACK]}, 404, "No vendor has the id V999."),
        (
            {"vendor_id": "V006", "lines": [RACK]},
            409,
            "No purchase order can be raised for a suspended vendor, and Nova Component Systems Pvt Ltd (V006) is "
            "suspended.",
        ),
        ({"vendor_id": "V001", "lines": []}, 422, "A purchase order needs between 1 and 20 lines."),
        ({"vendor_id": "V001", "lines": [RACK] * 21}, 422, "A purchase order needs between 1 and 20 lines."),
        ({"vendor_id": "V001", "lines": [RACK, RACK | {"text": " "}]}, 422, "Line 2 needs a description."),
        ({"vendor_id": "V001", "lines": [RACK | {"qty": 0}]}, 422, "Line 1 needs a quantity above zero."),
        ({"vendor_id": "V001", "lines": [RACK | {"price": "-1"}]}, 422, "Line 1 needs a unit price above zero."),
        ({"vendor_id": "V001", "lines": [RACK | {"tax": 101}]}, 422, "Line 1 needs a tax rate from 0 to 100 percent."),
        ({"vendor_id": "V001", "lines": [RACK | {"tax": -1}]}, 422, "Line 1 needs a tax rate from 0 to 100 percent."),
    ],
)
async def test_invalid_order_is_refused(
    client: httpx.AsyncClient, body: dict[str, Any], code: int, detail: str
) -> None:
    status, response = await post(client, "/v1/orders", body)

    assert (status, response) == (code, {"detail": detail})
    assert stored("SELECT count(*) FROM orders") == [(27,)]


async def test_same_domestic_account_at_another_bank_is_another_payee(client: httpx.AsyncClient) -> None:
    status, vendor = await post(client, "/v1/vendors", KESTREL | {"account": "5020-0012-3456-78"})

    assert (status, vendor["id"]) == (201, "V014")


async def test_foreign_vendor_needs_no_tax_id_and_an_iban_needs_no_bank_code(client: httpx.AsyncClient) -> None:
    body = {
        "name": "Atelier Rousseau SARL",
        "country": "FR",
        "tax_id": "",
        "account": "FR76 3000 6000 0112 3456 7890 189",
        "bank_code": "",
        "bank": "Crédit Agricole",
        "currency": "EUR",
    }
    status, vendor = await post(client, "/v1/vendors", body)

    assert (status, vendor["country"], vendor["currency"], vendor["tax_id"]) == (201, "FR", "EUR", "")
