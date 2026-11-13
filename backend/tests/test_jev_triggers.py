"""
When the PO stage hands an invoice line to Jev, and when it must not: one case per condition.

No model is called. Every invoice is a saved reading typed in by hand (manual entry), and Jev is a spy that records
what it is asked and answers nothing useful (0.02, i.e. "no"), so these tests only check the trigger: whether Jev is
asked, about which invoice lines, and which PO lines it is offered.
"""

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from conftest import DATA
from test_assist import FakeJev, brightline, codes, run, step

from app.entities import EntitiesRegistry, StageTypes
from app.entities.stages.po_jev import PoJevClass

pytestmark = pytest.mark.usefixtures("client")


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> FakeJev:
    """A Jev that records every request on the pipeline's own Jev instance."""
    fake = FakeJev()
    monkeypatch.setattr(EntitiesRegistry.Jev, "_jev_adapter", fake)
    return fake


def typed(sample: str, **changes: object) -> dict[str, Any]:
    """A sample's saved reading, typed in by hand (no file, no model), with some fields replaced."""
    raw = json.loads((DATA / "invoices" / f"{sample}.read.json").read_text())
    raw.pop("sources")
    return raw | changes


def line(description: str, quantity: str, price: str) -> dict[str, str]:
    """One printed invoice line at 18% GST."""
    amount = f"{Decimal(quantity) * Decimal(price.replace(',', '')):.2f}"
    return {
        "description": description,
        "quantity": quantity,
        "unit": "pcs",
        "unit_price": price,
        "line_amount": amount,
        "tax_rate_pct": "18",
    }


def totals(*lines: dict[str, str]) -> dict[str, Any]:
    """Subtotal, CGST + SGST 9% each and total for these lines."""
    subtotal = sum(Decimal(item["line_amount"]) for item in lines)
    tax = (subtotal * Decimal("0.09")).quantize(Decimal("0.01"))
    return {
        "lines": list(lines),
        "subtotal": f"{subtotal:.2f}",
        "tax_lines": [
            {"label": "CGST", "rate_pct": "9", "amount": f"{tax:.2f}"},
            {"label": "SGST", "rate_pct": "9", "amount": f"{tax:.2f}"},
        ],
        "total": f"{subtotal + 2 * tax:.2f}",
    }


def asked(spy: FakeJev) -> list[tuple[int, str]]:
    """Every (invoice line, PO line) Jev was asked about, from the question names."""
    pairs = []
    for _, questions in spy.calls:
        for name in questions:
            number, po_number, po_line = name.removeprefix("line_").split("__")
            pairs.append((int(number), f"{po_number}/{po_line}"))
    return pairs


# Brightline at reset: PO-0159 laptop / dock / monitor / headset, PO-0160 desk / cabinet, PO-0161 whiteboard.
HEADSET = line("Wireless headset", "20", "3,200.00")
DESK = line("Height-adjustable office desk", "15", "9,000.00")
CORDLESS = line("Cordless headset", "20", "3,200.00")  # scores 81 against "Wireless headset": not sure


# 1. Jev is NOT asked: every line is sure, or the stage never reaches line matching.


@pytest.mark.parametrize(
    ("label", "changes"),
    [
        ("printed PO, exact line", {"po_reference_text": "PO-2024-0159"} | totals(HEADSET)),
        ("two printed POs, exact lines", {"po_reference_text": "PO-2024-0159 / PO-2024-0160"} | totals(HEADSET, DESK)),
        ("no PO, exact line on one PO", {"po_reference_text": ""} | totals(HEADSET)),
        ("no PO, exact lines on two POs", {"po_reference_text": ""} | totals(HEADSET, DESK)),
    ],
)
async def test_sure_lines_never_reach_jev(client: Any, spy: FakeJev, label: str, changes: dict[str, Any]) -> None:
    detail = await run(client, brightline(**changes))

    po = step(detail, "po")
    assert po is not None
    assert spy.calls == [], label
    assert {item["method"] for item in po["data"]["allocations"]} == {"code"}, label
    assert detail["outcome"] == "approved", label


@pytest.mark.parametrize(
    ("label", "sample", "changes", "code"),
    [
        (
            "unknown vendor",
            "INV-028",
            {"supplier_name": "Nobody Ltd", "supplier_tax_id": "", "bank_account_number": ""},
            "VENDOR_NOT_FOUND",
        ),
        ("suspended vendor, no open PO", "INV-014", {}, "VENDOR_SUSPENDED"),
        ("cancelled printed PO", "INV-030", {}, "PO_CANCELLED"),
        ("other vendor's printed PO", "INV-028", {"po_reference_text": "PO-2024-0142"}, "PO_VENDOR_MISMATCH"),
        ("mistyped printed PO", "INV-028", {"po_reference_text": "PO-24-0159"}, "PO_NOT_FOUND"),
        ("unknown printed PO", "INV-028", {"po_reference_text": "PO-2024-0999"}, "PO_NOT_FOUND"),
    ],
)
async def test_no_pool_means_no_jev(
    client: Any, spy: FakeJev, label: str, sample: str, changes: dict[str, Any], code: str
) -> None:
    body = typed(sample, **changes)
    if sample == "INV-028":
        body |= {"po_reference_text": changes.get("po_reference_text", "")} | totals(CORDLESS)

    detail = await run(client, body)

    assert spy.calls == [], label
    assert code in codes(detail), label


@pytest.mark.parametrize(
    ("label", "changes", "code"),
    [
        ("bank account differs", {"bank_account_number": "11112222333344"}, "BANK_ACCOUNT_MISMATCH"),
        ("invoice number missing", {"invoice_number": ""}, "MISSING_INVOICE_NUMBER"),
        ("tax ID differs from the master", {"supplier_tax_id": "29AAFCB7421K1Z9"}, "TAX_ID_MISMATCH"),
    ],
)
async def test_an_earlier_block_keeps_jev_out(
    client: Any, spy: FakeJev, label: str, changes: dict[str, Any], code: str
) -> None:
    detail = await run(client, brightline(po_reference_text="", **totals(CORDLESS, DESK), **changes))

    po = step(detail, "po")
    assert spy.calls == [], label
    assert code in codes(detail), label
    if po is not None and po["state"] == "done" and po["data"]["allocations"]:
        assert [item["method"] for item in po["data"]["allocations"]] == ["unmatched", "code"], label


async def test_an_exact_duplicate_keeps_jev_out(client: Any, spy: FakeJev) -> None:
    first = await run(client, brightline(po_reference_text="", **totals(HEADSET)))
    again = await run(client, brightline(po_reference_text="", **totals(CORDLESS)))

    assert first["outcome"] == "approved"
    assert spy.calls == []
    assert "DUPLICATE_EXACT" in codes(again)


async def test_an_invoice_without_lines_never_reaches_jev(client: Any, spy: FakeJev) -> None:
    detail = await run(client, brightline(po_reference_text="", lines=[], subtotal="1000.00", total="1180.00"))

    assert spy.calls == []
    assert "PO_NOT_FOUND" in codes(detail)


# 2. Jev IS asked: only about the lines the code is not sure of, and only about pool lines with something left.


async def test_a_reworded_line_is_asked_about_every_pool_line(client: Any, spy: FakeJev) -> None:
    await run(client, brightline(po_reference_text="", **totals(CORDLESS)))

    assert len(spy.calls) == 1
    assert {number for number, _ in asked(spy)} == {1}
    assert sorted(key for _, key in asked(spy)) == sorted(
        [f"PO-2024-0159/{k}" for k in (1, 2, 3, 4)] + ["PO-2024-0160/1", "PO-2024-0160/2", "PO-2024-0161/1"]
    )


async def test_only_the_unsure_line_of_a_mixed_invoice_is_asked(client: Any, spy: FakeJev) -> None:
    await run(client, brightline(po_reference_text="", **totals(DESK, CORDLESS)))

    assert {number for number, _ in asked(spy)} == {2}  # line 1 (the desk) is sure


async def test_a_printed_po_restricts_what_jev_is_offered(client: Any, spy: FakeJev) -> None:
    await run(client, brightline(po_reference_text="PO-2024-0159", **totals(CORDLESS)))

    assert {key.split("/")[0] for _, key in asked(spy)} == {"PO-2024-0159"}


async def test_a_line_on_another_po_than_the_printed_one_offers_only_the_printed_po(client: Any, spy: FakeJev) -> None:
    detail = await run(client, brightline(po_reference_text="PO-2024-0160", **totals(HEADSET)))

    assert {key.split("/")[0] for _, key in asked(spy)} == {"PO-2024-0160"}
    assert {"LINES_NOT_MATCHED", "PO_UNUSED"} <= codes(detail)


async def test_the_same_item_on_two_pos_is_asked(client: Any, spy: FakeJev) -> None:
    chairs = line("Stackable visitor chair", "30", "2,600.00")  # ordered on both of Kaveri's open POs
    await run(client, typed("INV-034", po_reference_text="", invoice_number="KOI/24-25/0219", **totals(chairs)))

    keys = {key for _, key in asked(spy)}
    assert {"PO-2024-0165/1", "PO-2024-0166/1"} <= keys


async def test_a_line_on_no_po_is_asked(client: Any, spy: FakeJev) -> None:
    detail = await run(
        client, brightline(po_reference_text="", **totals(line("Annual maintenance contract", "1", "45,000.00")))
    )

    assert len(spy.calls) == 1
    assert "LINES_NOT_MATCHED" in codes(detail)


async def test_used_up_po_lines_are_not_offered(client: Any, spy: FakeJev) -> None:
    laptops = line("Business laptop (14 inch)", "10", "52,000.00")
    docks = line("USB-C docking station", "10", "6,500.00")
    first = await run(
        client, brightline(po_reference_text="PO-2024-0159", invoice_number="BLI/X/1", **totals(laptops, docks))
    )
    await run(client, brightline(po_reference_text="", **totals(CORDLESS)))

    assert first["outcome"] == "approved"
    keys = {key for _, key in asked(spy)}
    assert "PO-2024-0159/1" not in keys
    assert "PO-2024-0159/2" not in keys
    assert "PO-2024-0159/4" in keys


# 3. The thresholds themselves: sure only above 90 with no other pool line at 70 or more.


class ScoredOrders:
    """A PO entity whose scorer returns fixed scores per PO line, to test the thresholds exactly."""

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def score_line(self, pool: list[Any], invoice_line: Any) -> list[StageTypes.LineCandidate]:
        return sorted(
            (
                StageTypes.LineCandidate(
                    po_number=key.split("/")[0],
                    po_line=int(key.split("/")[1]),
                    po_date=date(2024, 10, 1),
                    description=key,
                    score=score,
                    left=Decimal(5),
                )
                for key, score in self.scores.items()
            ),
            key=lambda item: -item.score,
        )


@pytest.mark.parametrize(
    ("scores", "sure"),
    [
        ({"A/1": 100.0}, True),
        ({"A/1": 90.01}, True),
        ({"A/1": 90.0}, False),  # 90 itself is not sure
        ({"A/1": 100.0, "A/2": 70.0}, False),  # a look-alike on the same PO at 70
        ({"A/1": 100.0, "B/1": 70.0}, False),  # a look-alike on another PO at 70
        ({"A/1": 100.0, "B/1": 69.99}, True),  # below the candidate cut-off
        ({"A/1": 40.0}, False),  # nothing resembles it
    ],
)
def test_sure_needs_above_90_and_no_look_alike_at_70(scores: dict[str, float], sure: bool) -> None:
    stage = EntitiesRegistry.PoStage
    original = stage._order_entity
    stage._order_entity = ScoredOrders(scores)  # type: ignore[assignment]
    try:
        plans = stage.plan(
            [], [StageTypes.PricedLine(description="x", quantity=Decimal(1), unit_price=None, tax_rate_pct=None)]
        )
    finally:
        stage._order_entity = original

    assert plans[0].sure is sure


def test_question_names_carry_the_invoice_line_and_the_po_line() -> None:
    assert PoJevClass.line_question(2, "PO-2024-0165/1") == "line_2__PO-2024-0165__1"
