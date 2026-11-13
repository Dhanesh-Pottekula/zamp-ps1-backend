"""
Real-world invoice wording against real-world POs: does the PO stage decide the line itself or ask Jev, and how does
the invoice end?

Each scenario creates its own vendor and POs through the API, then enters an invoice by hand (no model is called).
Jev is a fake whose answers each scenario sets, keyed by a PO line's label, so a test can say "Jev keeps the 27-inch
monitor" without knowing the PO numbers the API will hand out.
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import DATA
from test_assist import FakeJev, codes, run, step

from app.entities import EntitiesRegistry

type Line = tuple[str, str, int, str]  # label, description, quantity, unit price


@dataclass(frozen=True)
class Scenario:
    """One vendor's POs, one invoice, Jev's answers and what must happen."""

    pos: list[list[Line]]
    invoice: list[tuple[str, str, str]]  # description, quantity, unit price
    printed: list[int] | None = None  # indexes of the POs printed on the invoice, None = nothing printed
    jev: dict[str, float] = field(default_factory=dict)  # "n:label" -> p(yes); anything else 0.02
    asked: set[int] = field(default_factory=set)  # invoice lines Jev must be asked about (empty: no Jev call)
    outcome: str = "approved"
    methods: list[str] | None = None  # how each allocation was settled, in order
    must: set[str] = field(default_factory=set)  # finding codes that must appear
    po_dates: list[int] | None = None  # days from today per PO (default 0 = today)


SCENARIOS: dict[str, Scenario] = {
    # The code is sure: Jev is not asked.
    "SKU prefix on the invoice": Scenario(
        pos=[[("headset", "Wireless headset", 20, "3200")]],
        invoice=[("WH-200 Wireless Headset", "10", "3200")],
        methods=["code"],
    ),
    "typo on the invoice": Scenario(
        pos=[[("headset", "Wireless headset", 20, "3200")]],
        invoice=[("Wirless headset", "10", "3200")],
        methods=["code"],
    ),
    "upper case and plural": Scenario(
        pos=[[("headset", "Wireless headset", 20, "3200")]],
        invoice=[("WIRELESS HEADSETS", "10", "3200")],
        methods=["code"],
    ),
    "construction material reworded": Scenario(
        pos=[[("cement", "OPC cement 53 grade (50 kg)", 400, "380")]],
        invoice=[("Cement OPC 53 grade 50kg bag", "200", "380")],
        methods=["code"],
    ),
    "items in a different order than the PO": Scenario(
        pos=[[("desk", "Height-adjustable office desk", 15, "9000"), ("headset", "Wireless headset", 20, "3200")]],
        invoice=[("Wireless headset", "20", "3200"), ("Height-adjustable office desk", "15", "9000")],
        printed=[0],
        methods=["code", "code"],
    ),
    "same item in two invoice lines (two deliveries)": Scenario(
        pos=[[("headset", "Wireless headset", 20, "3200")]],
        invoice=[("Wireless headset", "10", "3200"), ("Wireless headset", "10", "3200")],
        methods=["code", "code"],
    ),
    "two deliveries together exceed the PO": Scenario(
        pos=[[("headset", "Wireless headset", 20, "3200")]],
        invoice=[("Wireless headset", "15", "3200"), ("Wireless headset", "10", "3200")],
        outcome="needs_attention",
        methods=["code", "code"],
        must={"QTY_EXCEEDS_ORDERED"},
    ),
    # The code is not sure: Jev decides.
    "verbose description with model and colour": Scenario(
        pos=[[("headset", "Wireless headset", 20, "3200")]],
        invoice=[("Wireless Headset (Model WH-200, black)", "10", "3200")],
        jev={"1:headset": 0.95},
        asked={1},
        methods=["jev_kept"],
    ),
    "short word inside a different item (the trap)": Scenario(
        pos=[[("arm", "Monitor arm (dual, gas spring)", 40, "2450")]],
        invoice=[("Monitor", "10", "2450")],  # same price, so only Jev can stop it
        jev={},  # Jev: a monitor is not a monitor arm
        asked={1},
        outcome="needs_attention",
        methods=["jev_dropped"],
        must={"LINES_NOT_MATCHED"},
    ),
    "two monitor sizes on one PO": Scenario(
        pos=[[("m24", "Monitor (24 inch IPS)", 20, "11000"), ("m27", "Monitor (27 inch IPS)", 10, "16500")]],
        invoice=[('Monitor 27"', "5", "16500")],
        printed=[0],
        jev={"1:m27": 0.96},
        asked={1},
        methods=["jev_kept"],
    ),
    "two toner models, Jev picks the right one": Scenario(
        pos=[[("85a", "Toner cartridge (HP 85A)", 30, "4200"), ("26a", "Toner cartridge (HP 26A)", 30, "6800")]],
        invoice=[("HP 26A toner", "10", "6800")],
        jev={"1:26a": 0.94},
        asked={1},
        methods=["jev_kept"],
    ),
    "two toner models, Jev picks the wrong one": Scenario(
        pos=[[("85a", "Toner cartridge (HP 85A)", 30, "4200"), ("26a", "Toner cartridge (HP 26A)", 30, "6800")]],
        invoice=[("HP 26A toner", "10", "6800")],
        jev={"1:85a": 0.94},  # wrong item: the price check still holds it
        asked={1},
        outcome="needs_attention",
        methods=["jev_changed"],
        must={"UNIT_PRICE_MISMATCH"},
    ),
    "steel bar sizes on two POs, no PO printed": Scenario(
        pos=[[("b12", "TMT steel bar 12 mm", 500, "62")], [("b16", "TMT steel bar 16 mm", 500, "64")]],
        invoice=[("TMT 16mm", "200", "64")],
        jev={"1:b16": 0.93},
        asked={1},
        methods=["jev_kept"],  # the code's weak guess (59) was already the 16 mm bar; Jev confirms it
    ),
    "service abbreviated differently": Scenario(
        pos=[[("amc", "AMC - air conditioners (12 months)", 1, "84000")]],
        invoice=[("Annual maintenance - AC units", "1", "84000")],
        jev={"1:amc": 0.92},
        asked={1},
        methods=["jev_kept"],
    ),
    "goods plus a freight line": Scenario(
        pos=[[("headset", "Wireless headset", 20, "3200")]],
        invoice=[("Wireless headset", "10", "3200"), ("Freight & handling", "1", "1500")],
        jev={},  # freight is on no PO line
        asked={2},  # the headset is sure; only the freight line goes to Jev
        outcome="needs_attention",
        methods=["code", "jev_dropped"],
        must={"LINES_NOT_MATCHED"},
    ),
    "same chair on two POs at different prices": Scenario(
        pos=[
            [("chair_a", "Stackable visitor chair", 40, "2600")],
            [("chair_b", "Stackable visitor chair", 60, "2800")],
        ],
        invoice=[("Stackable visitor chair", "30", "2800")],
        jev={"1:chair_b": 0.95},
        asked={1},
        methods=["jev_changed"],  # the code suggested the older PO's chair
    ),
    "same chair on two POs, invoice price matches neither": Scenario(
        pos=[
            [("chair_a", "Stackable visitor chair", 40, "2600")],
            [("chair_b", "Stackable visitor chair", 60, "2800")],
        ],
        invoice=[("Stackable visitor chair", "30", "2700")],
        jev={"1:chair_a": 0.95, "1:chair_b": 0.95},  # price rules nothing out: both are right, oldest first
        asked={1},
        outcome="needs_attention",
        methods=["jev_kept"],
        must={"UNIT_PRICE_MISMATCH"},
    ),
    "same chair and price on two POs, the older has too few left": Scenario(
        pos=[
            [("chair_a", "Stackable visitor chair", 10, "2800")],
            [("chair_b", "Stackable visitor chair", 60, "2800")],
        ],
        invoice=[("Stackable visitor chair", "30", "2800")],
        jev={"1:chair_a": 0.95, "1:chair_b": 0.95},  # both right: the older gives its 10, the newer the other 20
        asked={1},
        methods=["jev_kept", "jev_changed"],
    ),
    "the PO at the invoice price has too few left": Scenario(
        pos=[
            [("chair_a", "Stackable visitor chair", 40, "2600")],
            [("chair_b", "Stackable visitor chair", 10, "2800")],
        ],
        invoice=[("Stackable visitor chair", "30", "2800")],
        jev={"1:chair_b": 0.95},  # the price picks PO-B even though PO-A has enough left
        asked={1},
        outcome="needs_attention",
        methods=["jev_changed"],
        must={"QTY_EXCEEDS_ORDERED"},
    ),
    "reworded line at a raised price, only one PO line": Scenario(
        pos=[[("headset", "Wireless headset", 20, "3200")]],
        invoice=[("Hdst wireless", "10", "3500")],
        jev={"1:headset": 0.95},  # the only headset line: price rules nothing out, the price check reports it
        asked={1},
        outcome="needs_attention",
        methods=["jev_kept"],
        must={"UNIT_PRICE_MISMATCH"},
    ),
    # Where the pool cannot hold the right PO.
    "the only matching PO is older than the ten searched": Scenario(
        pos=[[("valve", "Gate valve 2 inch (brass)", 50, "1800")]]
        + [[(f"filler{i}", f"Spare part type {i}", 5, "100")] for i in range(10)],
        po_dates=[-30] + [-20 + i for i in range(10)],
        invoice=[("Gate valve 2 inch (brass)", "10", "1800")],
        jev={},  # the valve PO is not among the ten newest, so Jev is offered only spare parts
        asked={1},
        outcome="needs_attention",
        must={"LINES_NOT_MATCHED"},
    ),
    "invoice dated before the PO": Scenario(
        pos=[[("headset", "Wireless headset", 20, "3200")]],
        po_dates=[5],
        invoice=[("Wireless headset", "10", "3200")],
        outcome="needs_attention",
        must={"PO_NOT_FOUND"},
    ),
}


@pytest.fixture
def jev(monkeypatch: pytest.MonkeyPatch) -> FakeJev:
    fake = FakeJev()
    monkeypatch.setattr(EntitiesRegistry.Jev, "_jev_adapter", fake)
    return fake


async def create(client: Any, index: int, scenario: Scenario) -> tuple[dict[str, Any], dict[str, str]]:
    """Create the scenario's vendor and POs; return the vendor and label -> PO-YYYY-NNNN/k."""
    letters = "".join(chr(ord("A") + (index * 7 + offset) % 26) for offset in range(5))
    vendor_body = {
        "name": f"Realworld Supplier {index} Pvt Ltd",
        "tax_id": f"29{letters}{1000 + index:04d}F1Z5",
        "account": f"77{index:04d}0099",
        "bank_code": "HDFC0001111",
        "bank": "HDFC Bank",
    }
    response = await client.post("/v1/vendors", json=vendor_body)
    assert response.status_code == 201, response.text
    vendor = response.json()
    labels: dict[str, str] = {}
    for number, po_lines in enumerate(scenario.pos):
        days = (scenario.po_dates or [0] * len(scenario.pos))[number]
        body = {
            "vendor_id": vendor["id"],
            "date": (date.today() + timedelta(days=days)).isoformat(),
            "lines": [{"text": text, "qty": qty, "price": price, "tax": 18} for _, text, qty, price in po_lines],
        }
        response = await client.post("/v1/orders", json=body)
        assert response.status_code == 201, response.text
        order = response.json()
        for line_no, (label, *_rest) in enumerate(po_lines, start=1):
            labels[label] = f"{order['po']}/{line_no}"

    return vendor_body | {"id": vendor["id"]}, labels


def invoice(index: int, vendor: dict[str, Any], scenario: Scenario, labels: dict[str, str]) -> dict[str, Any]:
    """The invoice as a person would type it, at 18% GST, dated today."""
    raw = json.loads((DATA / "invoices" / "INV-001.read.json").read_text())
    raw.pop("sources")
    lines = [
        {
            "description": text,
            "quantity": quantity,
            "unit": "pcs",
            "unit_price": price,
            "line_amount": f"{Decimal(quantity) * Decimal(price):.2f}",
            "tax_rate_pct": "18",
        }
        for text, quantity, price in scenario.invoice
    ]
    subtotal = sum(Decimal(item["line_amount"]) for item in lines)
    tax = (subtotal * Decimal("0.09")).quantize(Decimal("0.01"))
    printed = sorted({labels[po[0][0]].split("/")[0] for po in (scenario.pos[i] for i in scenario.printed or [])})
    return raw | {
        "supplier_name": vendor["name"],
        "supplier_tax_id": vendor["tax_id"],
        "bank_account_number": vendor["account"],
        "bank_code": vendor["bank_code"],
        "invoice_number": f"RW/{index:03d}",
        "invoice_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=30)).isoformat(),
        "po_reference_text": " / ".join(printed),
        "lines": lines,
        "subtotal": f"{subtotal:.2f}",
        "tax_lines": [
            {"label": "CGST", "rate_pct": "9", "amount": f"{tax:.2f}"},
            {"label": "SGST", "rate_pct": "9", "amount": f"{tax:.2f}"},
        ],
        "total": f"{subtotal + 2 * tax:.2f}",
    }


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_real_world_wording(client: Any, jev: FakeJev, name: str) -> None:
    index = list(SCENARIOS).index(name) + 1
    scenario = SCENARIOS[name]
    vendor, labels = await create(client, index, scenario)
    jev.answers = {f"{key.split(':')[0]}:{labels[key.split(':')[1]]}": value for key, value in scenario.jev.items()}

    detail = await run(client, invoice(index, vendor, scenario, labels))

    asked = {int(name.removeprefix("line_").split("__")[0]) for _, questions in jev.calls for name in questions}
    assert asked == scenario.asked, f"{name}: Jev asked about lines {asked}, expected {scenario.asked}"
    assert detail["outcome"] == scenario.outcome, f"{name}: {detail['summary']}"
    assert scenario.must <= codes(detail), f"{name}: {codes(detail)}"
    if scenario.methods is not None:
        po = step(detail, "po")
        assert po is not None
        assert [item["method"] for item in po["data"]["allocations"]] == scenario.methods, name
