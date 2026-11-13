"""PO matching by lines (printed POs or the vendor's open POs), the pool and consumption."""

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from conftest import extraction

from app.adapters.match_adp import MatchAdapterClass
from app.entities import EntitiesRegistry, StageTypes
from app.entities.stages.po_stage import _Helper as OrderHelper

if TYPE_CHECKING:
    from app.adapters import AdapterRegistry


def priced(description: str, price: str, quantity: str = "1") -> list[StageTypes.PricedLine]:
    return [
        StageTypes.PricedLine(
            description=description, quantity=Decimal(quantity), unit_price=Decimal(price), tax_rate_pct=Decimal(18)
        )
    ]


def vendor_match(vendor_id: str | None) -> StageTypes.VendorMatch:
    return StageTypes.VendorMatch(
        vendor_id=vendor_id,
        legal_name="Test vendor" if vendor_id else None,
        method="tax_id" if vendor_id else "none",
        score=100.0 if vendor_id else 0.0,
        needed=80.0,
        evidence=[],
        candidates=[],
        findings=[],
    )


async def match(
    registry: type[EntitiesRegistry],
    reference: str | None,
    vendor_id: str | None,
    taxable: str,
    lines: list[StageTypes.PricedLine] | None = None,
) -> StageTypes.OrderMatch:
    """Run stage 6 on a reading with this PO reference and these priced lines (no Jev: no adapter, no recording)."""
    reading = extraction("INV-001", po_reference_text=reference or "", invoice_date="2024-11-20")
    return await registry.PoStage.run(
        reading, vendor_match(vendor_id), lines or [], taxable=Decimal(taxable), earlier=[]
    )


def codes(result: StageTypes.OrderMatch) -> list[tuple[str, str]]:
    return [(finding.code, finding.severity) for finding in result.findings]


STAND = "Laptop stand (adjustable aluminium)"


async def test_explicit_reference_matches(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, "P.O. No: po/2024/0142", "V001", "36000", priced(STAND, "1200", "30"))

    assert (result.po_number, result.method) == ("PO-2024-0142", "explicit")
    assert codes(result) == [("PO_MATCHED", "info")]
    assert [(item.po_number, item.po_line, item.quantity, item.method) for item in result.allocations] == [
        ("PO-2024-0142", 1, Decimal(30), "code")
    ]


async def test_reference_in_line_description_and_bare_number(registry: type[EntitiesRegistry]) -> None:
    in_line = await match(registry, None, "V001", "36000", priced("Laptop stand against PO 2024 0142", "1200"))
    bare = await match(registry, "Order 2024-0142", "V001", "36000", priced(STAND, "1200"))

    assert in_line.po_number == "PO-2024-0142"
    assert bare.po_number == "PO-2024-0142"


async def test_unknown_reference_is_not_found(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, "PO-2024-9999", "V001", "36000", priced(STAND, "1200"))

    assert result.po_number is None
    assert codes(result) == [("PO_NOT_FOUND", "block")]
    assert result.findings[0].data["cause"] == "refused"


async def test_other_vendors_po_is_refused(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, "PO-2024-0142", "V002", "36000", priced(STAND, "1200"))

    assert result.po_number is None
    assert codes(result) == [("PO_VENDOR_MISMATCH", "block")]


async def test_cancelled_po_is_refused(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, "PO-2024-0149", "V005", "5000", priced("Business cards (box of 100)", "2.5"))

    assert result.po_number is None
    assert codes(result) == [("PO_CANCELLED", "block")]


async def test_fully_invoiced_po_still_matches(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, "PO-2024-0148", "V002", "15000", priced("Standing desk (electric)", "15000"))

    assert (result.po_number, result.method) == ("PO-2024-0148", "explicit")
    assert (result.score, result.needed) == (100.0, 80.0)


async def test_exact_lines_match_the_sole_open_po_without_a_printed_number(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, None, "V003", "45000", priced("USB-C docking connector module", "150", "300"))

    assert (result.po_number, result.method, result.score) == ("PO-2024-0144", "lines", 100.0)
    assert codes(result) == [("PO_MATCHED", "info")]
    assert result.pool == ["PO-2024-0144"]


async def test_a_vague_line_without_jev_is_not_matched(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, None, "V004", "81000", priced("Freight charges, October consignment", "81000"))

    assert result.po_number is None
    assert set(result.pool) == {"PO-2024-0145", "PO-2024-0146"}
    assert codes(result) == [("LINES_NOT_MATCHED", "block")]
    assert [item.method for item in result.allocations] == ["unmatched"]


async def test_no_reference_and_unknown_vendor_is_not_found(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, None, None, "1000", priced("Anything", "1000"))

    assert codes(result) == [("PO_NOT_FOUND", "block")]
    assert result.findings[0].data["cause"] == "no_vendor"


async def test_an_invoice_without_lines_is_never_approved(registry: type[EntitiesRegistry]) -> None:
    unprinted = await match(registry, None, "V003", "45000")
    printed = await match(registry, "PO-2024-0144", "V003", "45000")

    assert codes(unprinted) == [("PO_NOT_FOUND", "block")]
    assert unprinted.findings[0].data["cause"] == "no_lines"
    assert codes(printed) == [("PO_UNUSED", "block")]


async def test_a_vendor_without_open_pos_has_no_pool(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, None, "V006", "32000", priced("Proximity sensor M12 (PNP)", "640", "50"))

    assert codes(result) == [("PO_NOT_FOUND", "block")]
    assert result.findings[0].data["cause"] == "no_open_po"


async def test_pool_is_open_dated_capped_and_newest_first(registry: type[EntitiesRegistry]) -> None:
    reading = extraction("INV-001", po_reference_text="", invoice_date="2024-10-31")
    lines = priced("Monitor (24 inch IPS)", "11000")

    pool, printed, refusals = await registry.PoStage.pool(reading, lines, "V010")
    pixel, _, _ = await registry.PoStage.pool(
        reading.model_copy(update={"invoice_date": date(2026, 1, 1)}), lines, "V005"
    )

    assert ([order.po_number for order in pool], printed, refusals) == (["PO-2024-0160", "PO-2024-0159"], [], [])
    assert "PO-2024-0149" not in {order.po_number for order in pixel}


async def test_a_raised_price_still_lands_on_its_line(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, "PO-2024-0142", "V001", "13000", priced(STAND, "1300.00", "10"))
    order = await registry.Order.snapshot("PO-2024-0142")
    assert order is not None
    checked = registry.AmountsStage.run(
        result,
        [order],
        Decimal(13000),
        reading=extraction("INV-001"),
        priced=priced(STAND, "1300.00", "10"),
        pct=Decimal(2),
        invoice_rate=Decimal(18),
    )

    assert [(item.po_line, item.method) for item in result.allocations] == [(1, "code")]
    assert "UNIT_PRICE_MISMATCH" in [finding.code for finding in checked.findings]


async def test_consumption_moves_status_and_balance(registry: type[EntitiesRegistry], adapter: AdapterRegistry) -> None:
    async def consume(po_number: str, amount: str, quantity: str) -> StageTypes.OrderSnapshot:
        update = StageTypes.OrderUpdate(
            po_number=po_number,
            amount=Decimal(amount),
            remaining_after=Decimal(0),
            quantities={1: Decimal(quantity)},
        )
        await adapter.DB.run(lambda session: registry.Order.consume_in(session, update))
        snapshot = await registry.Order.snapshot(po_number)
        assert snapshot is not None
        return snapshot

    partial = await consume("PO-2024-0142", "36000", "30")
    full = await consume("PO-2024-0142", "24000", "20")
    cancelled = await consume("PO-2024-0149", "100", "40")

    assert (partial.status, partial.remaining, partial.lines[0].consumed_qty) == (
        "partially_invoiced",
        Decimal(24000),
        Decimal(30),
    )
    assert (full.status, full.remaining, full.lines[0].consumed_qty) == ("fully_invoiced", Decimal(0), Decimal(50))
    assert cancelled.status == "cancelled"


async def test_seed_restores_consumption(registry: type[EntitiesRegistry]) -> None:
    orders = {order.po_number: order for order in await registry.Order.snapshots()}

    assert orders["PO-2024-0148"].status == "fully_invoiced"
    assert orders["PO-2024-0148"].remaining == Decimal(0)
    assert orders["PO-2024-0149"].status == "cancelled"
    assert orders["PO-2024-0142"].remaining == Decimal(60000)


def test_only_distinct_numbers_in_the_reference_text_count() -> None:
    assert OrderHelper.references("PO-2024-0159 / PO-2024-0160") == ["PO-2024-0159", "PO-2024-0160"]
    assert OrderHelper.references("Order 2024-0160 and PO 2024 0159") == ["PO-2024-0160", "PO-2024-0159"]
    assert OrderHelper.references("PO-2024-0159, P.O. po/2024/0159") == ["PO-2024-0159"]
    assert OrderHelper.references(None) == []


BRIGHTLINE = [
    *priced("Wireless headset", "3200", "20"),
    *priced("Height-adjustable office desk", "9000", "15"),
]


async def test_two_printed_pos_of_the_vendor_both_match(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, "PO-2024-0159 / PO-2024-0160", "V010", "199000", BRIGHTLINE)

    assert (result.po_number, result.others) == ("PO-2024-0159", ["PO-2024-0160"])
    assert (result.method, result.score) == ("explicit", 100.0)
    assert codes(result) == [("PO_MATCHED", "info")]


async def test_the_same_lines_without_a_printed_number_match_by_code(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, None, "V010", "199000", BRIGHTLINE)

    assert (result.po_number, result.others, result.method) == ("PO-2024-0159", ["PO-2024-0160"], "lines")
    assert [(item.po_number, item.po_line) for item in result.allocations] == [
        ("PO-2024-0159", 4),
        ("PO-2024-0160", 1),
    ]


async def test_one_refused_po_refuses_them_all(registry: type[EntitiesRegistry]) -> None:
    unknown = await match(registry, "PO-2024-0159 / PO-2024-0999", "V010", "199000", BRIGHTLINE)
    foreign = await match(registry, "PO-2024-0159 / PO-2024-0142", "V010", "199000", BRIGHTLINE)
    cancelled = await match(registry, "PO-2024-0147 / PO-2024-0149", "V005", "5000", BRIGHTLINE)

    assert [(result.po_number, result.others) for result in (unknown, foreign, cancelled)] == [(None, [])] * 3
    assert codes(unknown) == [("PO_NOT_FOUND", "block")]
    assert codes(foreign) == [("PO_VENDOR_MISMATCH", "block")]
    assert codes(cancelled) == [("PO_CANCELLED", "block")]


async def test_printed_pos_restrict_the_pool(registry: type[EntitiesRegistry]) -> None:
    result = await match(registry, "PO-2024-0160", "V010", "55000", priced("Monitor (24 inch IPS)", "11000", "5"))

    assert result.pool == ["PO-2024-0160"]
    assert {code for code, _ in codes(result)} == {"LINES_NOT_MATCHED", "PO_UNUSED"}
    assert result.allocations[0].po_number is None


async def test_score_line_ranks_every_pool_line(registry: type[EntitiesRegistry]) -> None:
    pool = [await registry.Order.snapshot("PO-2024-0159"), await registry.Order.snapshot("PO-2024-0160")]
    line = StageTypes.PricedLine(description="Monitor (24 inch IPS)", quantity=None, unit_price=None, tax_rate_pct=None)

    scored = registry.Order.score_line([order for order in pool if order], line)

    assert len(scored) == 6
    assert (scored[0].po_number, scored[0].po_line, scored[0].score) == ("PO-2024-0159", 3, 100.0)
    assert scored[0].key == "PO-2024-0159/3"
    assert scored[0].left == 20


async def test_score_line_with_no_description_scores_zero(registry: type[EntitiesRegistry]) -> None:
    pool = [await registry.Order.snapshot("PO-2024-0159")]
    line = StageTypes.PricedLine(description="", quantity=None, unit_price=None, tax_rate_pct=None)

    assert {item.score for item in registry.Order.score_line([order for order in pool if order], line)} == {0.0}


async def test_po_matched_is_not_reported_beside_an_unused_printed_po(registry: type[EntitiesRegistry]) -> None:
    result = await match(
        registry, "PO-2024-0159 / PO-2024-0160", "V010", "64000", priced("Wireless headset", "3200", "20")
    )

    assert codes(result) == [("PO_UNUSED", "block")]


def test_same_item_needs_near_identical_text_and_the_same_numbers() -> None:
    match_adapter = MatchAdapterClass()

    assert match_adapter.same_item("Stackable visitor chair", "Stackable visitor chair")
    assert match_adapter.same_item("Stackable visitor chair", "Stackable Visitor Chairs")
    assert not match_adapter.same_item(
        "Freight - Bengaluru to Mumbai (October)", "Freight - Bengaluru to Delhi (October)"
    )
    assert not match_adapter.same_item("Business laptop (14 inch)", "Business laptop (15 inch)")


async def test_a_short_word_inside_a_longer_item_is_not_sure(registry: type[EntitiesRegistry]) -> None:
    """'Laptop stand' sits inside 'Laptop stand (adjustable aluminium)': full score, low coverage, so not sure."""
    pool = [await registry.Order.snapshot("PO-2024-0142")]
    plans = registry.PoStage.plan([order for order in pool if order], priced("Laptop stand", "1200", "10"))

    assert (plans[0].suggestion.score, plans[0].sure) == (100.0, False)  # type: ignore[union-attr]
    assert plans[0].suggestion.coverage < 80  # type: ignore[union-attr]
