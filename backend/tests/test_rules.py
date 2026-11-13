"""Validation, tolerance, line checks and the decision."""

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from conftest import extraction, line

from app.entities import FindingTypes, StageTypes

if TYPE_CHECKING:
    from conftest import StageRules

TODAY = date(2026, 11, 12)


def codes(findings: list[FindingTypes.Finding]) -> list[str]:
    return [finding.code for finding in findings]


def order(remaining: str = "60000.00", consumed_qty: str = "0", total: str = "60000.00") -> StageTypes.OrderSnapshot:
    return StageTypes.OrderSnapshot(
        po_number="PO-2024-0142",
        vendor_id="V001",
        vendor_name="Acme Supplies Pvt Ltd",
        po_date=date(2024, 10, 1),
        currency="INR",
        status="open",
        total=Decimal(total),
        consumed=Decimal(total) - Decimal(remaining),
        remaining=Decimal(remaining),
        lines=[
            StageTypes.OrderLine(
                line_no=1,
                description="Laptop stand (adjustable aluminium)",
                quantity=Decimal(50),
                unit="pcs",
                unit_price=Decimal("1200.00"),
                tax_rate_pct=Decimal(18),
                consumed_qty=Decimal(consumed_qty),
            )
        ],
    )


def priced(quantity: str = "30", price: str = "1200.00", rate: str | None = "18") -> list[StageTypes.PricedLine]:
    return [
        StageTypes.PricedLine(
            description="Laptop stand",
            quantity=Decimal(quantity),
            unit_price=Decimal(price),
            tax_rate_pct=Decimal(rate) if rate else None,
        )
    ]


def finding(code: FindingTypes.FindingCode, severity: FindingTypes.Severity) -> FindingTypes.Finding:
    return FindingTypes.Finding(code=code, severity=severity, message=code, data={})


def test_clean_invoice_validates(rules: StageRules) -> None:
    result = rules.validation.run(extraction("INV-001"), TODAY)

    assert result.findings == []
    assert result.taxable_value == Decimal("36000.00")
    assert result.tax_total == Decimal("6480.00")
    assert result.tax_rate_pct == Decimal(18)
    assert result.derived is False


def test_missing_values_are_flagged(rules: StageRules) -> None:
    result = rules.validation.run(
        extraction(invoice_number="", invoice_date="", subtotal="", total="", tax_lines=[]), TODAY
    )

    assert set(codes(result.findings)) >= {"MISSING_INVOICE_NUMBER", "MISSING_DATE", "MISSING_TOTAL"}
    assert {f.code: f.severity for f in result.findings}["MISSING_DATE"] == "warn"
    assert result.taxable_value is None


def test_future_date_and_bad_gstin_warn(rules: StageRules) -> None:
    result = rules.validation.run(extraction(invoice_date="2027-01-01", supplier_tax_id="29AACCA1234F1X5"), TODAY)

    assert codes(result.findings) == ["DATE_IN_FUTURE", "TAX_ID_FORMAT_INVALID"]


def test_sum_mismatches_warn(rules: StageRules) -> None:
    result = rules.validation.run(extraction(lines=[line(line_amount="35,000.00")], total="42,000.00"), TODAY)

    assert codes(result.findings) == ["LINE_SUM_MISMATCH", "TAX_SUM_MISMATCH"]


def test_tax_inclusive_total_derives_taxable_value_from_the_rate(rules: StageRules) -> None:
    inclusive = extraction(
        prices_include_tax=True,
        subtotal="",
        tax_lines=[],
        total="23,600",
        lines=[line(quantity="40", unit_price="590", line_amount="23,600", tax_rate_pct="5")],
    )

    result = rules.validation.run(inclusive, TODAY)
    lines = rules.validation.priced_lines(inclusive, result)

    assert result.derived is True
    assert result.taxable_value == Decimal("22476.19")
    assert result.tax_rate_pct == Decimal(5)
    assert codes(result.findings) == ["TAXABLE_VALUE_DERIVED"]
    assert lines[0].unit_price == Decimal("561.90")


def test_taxable_value_derived_from_tax_amounts(rules: StageRules) -> None:
    result = rules.validation.run(extraction(subtotal=""), TODAY)

    assert result.taxable_value == Decimal("36000.00")
    assert result.derived is True
    assert "LINE_SUM_MISMATCH" not in codes(result.findings)


def test_tolerance_is_two_percent_with_a_floor(rules: StageRules) -> None:
    assert rules.policy.tolerance(Decimal(60000), Decimal(2), "INR") == Decimal("1200.00")
    assert rules.policy.tolerance(Decimal(0), Decimal(2), "INR") == Decimal("500.00")
    assert rules.policy.tolerance(Decimal(0), Decimal(2), "USD") == Decimal("10.00")
    assert rules.policy.tolerance(Decimal(100), Decimal(2), "CHF") == Decimal("2.00")


def test_tolerance_boundary_on_both_sides(rules: StageRules) -> None:
    at_limit = rules.amounts.amount_check(
        order(),
        Decimal("61200.00"),
        currency="INR",
        lines=priced("50"),
        mapping=[1],
        pct=Decimal(2),
        invoice_rate=Decimal(18),
    )
    beyond = rules.amounts.amount_check(
        order(),
        Decimal("61200.01"),
        currency="INR",
        lines=priced("50"),
        mapping=[1],
        pct=Decimal(2),
        invoice_rate=Decimal(18),
    )

    assert at_limit.within is True
    assert codes(at_limit.findings) == ["AMOUNT_WITHIN_TOLERANCE", "PO_FULLY_INVOICED"]
    assert beyond.within is False
    assert codes(beyond.findings) == ["OVER_CONSUMPTION"]


def test_overage_inside_tolerance_is_stated_in_numbers(rules: StageRules) -> None:
    def message(amount: str) -> str:
        result = rules.amounts.amount_check(
            order(), Decimal(amount), currency="INR", lines=priced("50"), mapping=[1], pct=Decimal(2), invoice_rate=None
        )
        return result.findings[0].message

    assert message("61000.00") == (
        "The taxable value 61,000.00 exceeds the 60,000.00 left on PO-2024-0142 by 1,000.00, within the 1,200.00 "
        "tolerance."
    )
    assert message("60000.00") == (
        "The taxable value 60,000.00 is within the 60,000.00 left on PO-2024-0142 (tolerance 1,200.00)."
    )


def test_floor_applies_when_nothing_is_left(rules: StageRules) -> None:
    def check(amount: str) -> list[str]:
        result = rules.amounts.amount_check(
            order("0.00", "50"),
            Decimal(amount),
            currency=None,
            lines=[],
            mapping=[],
            pct=Decimal(2),
            invoice_rate=None,
        )
        return codes(result.findings)

    assert "AMOUNT_WITHIN_TOLERANCE" in check("500.00")
    assert "OVER_CONSUMPTION" in check("500.01")


def test_partial_invoice_is_never_an_error(rules: StageRules) -> None:
    result = rules.amounts.amount_check(
        order(), Decimal(36000), currency="INR", lines=priced(), mapping=[1], pct=Decimal(2), invoice_rate=Decimal(18)
    )

    assert codes(result.findings) == ["AMOUNT_WITHIN_TOLERANCE", "PARTIAL_DELIVERY"]
    assert result.level == "line"
    assert result.lines[0].ok is True


def test_unit_price_mismatch_on_a_partial(rules: StageRules) -> None:
    result = rules.amounts.amount_check(
        order(),
        Decimal(13000),
        currency="INR",
        lines=priced("10", "1300.00"),
        mapping=[1],
        pct=Decimal(2),
        invoice_rate=Decimal(18),
    )

    assert "PARTIAL_DELIVERY" in codes(result.findings)
    assert "UNIT_PRICE_MISMATCH" in codes(result.findings)
    assert result.lines[0].ok is False


def test_quantity_tax_rate_and_currency_checks(rules: StageRules) -> None:
    result = rules.amounts.amount_check(
        order("12000.00", "45"),
        Decimal(12000),
        currency="USD",
        lines=priced("10", "1200.00", "12"),
        mapping=[1],
        pct=Decimal(2),
        invoice_rate=Decimal(12),
    )

    assert {"CURRENCY_MISMATCH", "QTY_EXCEEDS_ORDERED", "TAX_RATE_MISMATCH"} <= set(codes(result.findings))


def test_unmapped_lines_fall_back_to_header_level(rules: StageRules) -> None:
    result = rules.amounts.amount_check(
        order(), Decimal(36000), currency="INR", lines=priced(), mapping=[None], pct=Decimal(2), invoice_rate=None
    )

    assert result.level == "header"
    assert result.lines == []
    assert "HEADER_LEVEL_ONLY" in codes(result.findings)


def test_decide_orders_reasons_and_picks_the_outcome(rules: StageRules) -> None:
    findings = [
        finding("AMOUNT_WITHIN_TOLERANCE", "info"),
        finding("LINE_SUM_MISMATCH", "warn"),
        finding("OVER_CONSUMPTION", "block"),
        finding("DUPLICATE_EXACT", "block"),
    ]

    rejected = rules.decision.run(findings, None, "PO-2024-0142")
    attention = rules.decision.run(findings[:3], None, "PO-2024-0142")

    assert rejected.outcome == "rejected"
    assert codes(rejected.reasons) == [
        "DUPLICATE_EXACT",
        "OVER_CONSUMPTION",
        "LINE_SUM_MISMATCH",
        "AMOUNT_WITHIN_TOLERANCE",
    ]
    assert rejected.update is None
    assert attention.outcome == "needs_attention"
    assert attention.update is None
    assert [(gate.name, gate.passed) for gate in attention.gates] == [
        ("vendor", False),
        ("bank", False),
        ("po", False),
        ("amount", False),
    ]


def test_approval_carries_the_po_update(rules: StageRules) -> None:
    amounts = rules.amounts.amount_check(
        order(), Decimal(36000), currency="INR", lines=priced(), mapping=[1], pct=Decimal(2), invoice_rate=Decimal(18)
    )

    vendor = StageTypes.VendorMatch(
        vendor_id="V001",
        legal_name="Acme Supplies Pvt Ltd",
        method="tax_id",
        score=100.0,
        needed=80.0,
        evidence=[],
        candidates=[],
        findings=[],
    )
    matched = StageTypes.OrderMatch(
        po_number="PO-2024-0142",
        method="explicit",
        signals=["reference"],
        score=100.0,
        needed=80.0,
        candidates=[],
        findings=[],
    )

    bank = StageTypes.BankCheck(
        vendor_id="V001", state="matched", invoice_account="**5678", master_account="**5678", findings=[]
    )

    decision = rules.decision.run(amounts.findings, amounts, "PO-2024-0142", vendor=vendor, bank=bank, order=matched)

    assert decision.outcome == "approved"
    assert [(gate.name, gate.passed) for gate in decision.gates] == [
        ("vendor", True),
        ("bank", True),
        ("po", True),
        ("amount", True),
    ]
    assert decision.summary == "Approved as partial delivery; 24,000.00 remains open on PO-2024-0142."
    assert decision.update is not None
    assert decision.update.remaining_after == Decimal(24000)
    assert decision.update.quantities == {1: Decimal(30)}


def test_a_date_whose_order_the_document_does_not_show_is_flagged(rules: StageRules) -> None:
    unclear = rules.validation.run(extraction(date_order=""), TODAY)
    settled = rules.validation.run(extraction(date_order="DMY"), TODAY)
    american = extraction(date_order="MDY")

    flagged = [finding for finding in unclear.findings if finding.code == "DATE_AMBIGUOUS"]
    assert [(finding.severity, finding.data["field"]) for finding in flagged] == [
        ("warn", "invoice_date"),
        ("warn", "due_date"),
    ]
    assert "04/11/2024" in flagged[0].message
    assert "DATE_AMBIGUOUS" not in codes(settled.findings)
    assert (american.invoice_date, american.due_date) == (date(2024, 4, 11), date(2024, 4, 12))


def test_gross_price_lines_are_checked_against_the_total(rules: StageRules) -> None:
    gross = extraction(
        "INV-012",
        lines=[line(quantity="8", unit_price="495.00", line_amount="3,960.00", tax_rate_pct="20")],
    )
    result = rules.validation.run(gross, TODAY)

    assert result.taxable_value == Decimal("3300.00")
    assert "LINE_SUM_MISMATCH" not in codes(result.findings)


def test_a_foreign_fifteen_character_tax_id_is_not_held_to_the_gstin_shape(rules: StageRules) -> None:
    result = rules.validation.run(extraction(supplier_tax_id="NL123456789B012"), TODAY)

    assert "TAX_ID_FORMAT_INVALID" not in codes(result.findings)


def test_two_vat_rates_are_not_added_into_one(rules: StageRules) -> None:
    mixed = extraction(
        "INV-011",
        lines=[
            line(quantity="10", unit_price="100,00", line_amount="1.000,00", tax_rate_pct="19"),
            line(quantity="10", unit_price="100,00", line_amount="1.000,00", tax_rate_pct="7"),
        ],
        subtotal="2.000,00",
        tax_lines=[
            {"label": "MwSt.", "rate_pct": "19", "amount": "190,00"},
            {"label": "MwSt.", "rate_pct": "7", "amount": "70,00"},
        ],
        total="2.260,00",
    )
    result = rules.validation.run(mixed, TODAY)

    assert (result.taxable_value, result.tax_total, result.tax_rate_pct) == (
        Decimal("2000.00"),
        Decimal("260.00"),
        None,
    )
    assert codes(result.findings) == []


def test_split_gst_still_adds_up_to_one_rate(rules: StageRules) -> None:
    assert rules.validation.run(extraction(), TODAY).tax_rate_pct == Decimal(18)


def test_an_indonesian_fifteen_digit_tax_id_is_not_a_gstin(rules: StageRules) -> None:
    result = rules.validation.run(extraction(supplier_tax_id="012345678901000"), TODAY)

    assert "TAX_ID_FORMAT_INVALID" not in codes(result.findings)


def chairs(remaining: str = "170000.00") -> StageTypes.OrderSnapshot:
    line = (
        order()
        .lines[0]
        .model_copy(
            update={"description": "Ergonomic office chair", "quantity": Decimal(20), "unit_price": Decimal("8500.00")}
        )
    )
    return order().model_copy(
        update={"po_number": "PO-2024-0143", "total": Decimal(170000), "remaining": Decimal(remaining), "lines": [line]}
    )


def split(
    rules: StageRules,
    taxable: str = "121000",
    second: StageTypes.OrderSnapshot | None = None,
    mapping: list[tuple[str, int] | None] | None = None,
) -> StageTypes.AmountCheck:
    chair = priced("10", "8500.00")[0].model_copy(update={"description": "Ergonomic office chair"})
    return rules.amounts.split_check(
        [order(), second or chairs()],
        Decimal(taxable),
        currency="INR",
        lines=[*priced(), chair],
        mapping=mapping or [("PO-2024-0142", 1), ("PO-2024-0143", 1)],
        pct=Decimal(2),
        invoice_rate=Decimal(18),
    )


def test_a_split_invoice_updates_each_po_with_its_share(rules: StageRules) -> None:
    amounts = split(rules)

    decision = rules.decision.run(amounts.findings, amounts, "PO-2024-0142")

    assert [(part.po_number, part.invoice_amount, part.within) for part in amounts.parts] == [
        ("PO-2024-0142", Decimal("36000.00"), True),
        ("PO-2024-0143", Decimal("85000.00"), True),
    ]
    assert (amounts.level, amounts.within, amounts.invoice_amount) == ("line", True, Decimal(121000))
    assert decision.outcome == "approved"
    assert decision.update == StageTypes.OrderUpdate(
        po_number="PO-2024-0142",
        amount=Decimal("36000.00"),
        remaining_after=Decimal("24000.00"),
        quantities={1: Decimal(30)},
    )
    assert decision.others == [
        StageTypes.OrderUpdate(
            po_number="PO-2024-0143",
            amount=Decimal("85000.00"),
            remaining_after=Decimal("85000.00"),
            quantities={1: Decimal(10)},
        )
    ]
    assert decision.summary == (
        "Approved and split between 2 POs: 36,000.00 on PO-2024-0142 (24,000.00 remains open) and 85,000.00 on "
        "PO-2024-0143 (85,000.00 remains open)."
    )


def test_a_split_with_one_po_over_its_balance_consumes_neither(rules: StageRules) -> None:
    amounts = split(rules, second=chairs(remaining="50000.00"))

    decision = rules.decision.run(amounts.findings, amounts, "PO-2024-0142")

    assert "OVER_CONSUMPTION" in codes(amounts.findings)
    assert amounts.within is False
    assert (decision.outcome, decision.update, decision.others) == ("needs_attention", None, [])


def test_an_invoice_that_cannot_be_split_with_certainty_is_held(rules: StageRules) -> None:
    stray = split(rules, mapping=[("PO-2024-0142", 1), None])
    unused = split(rules, mapping=[("PO-2024-0142", 1), ("PO-2024-0142", 1)])
    short = split(rules, taxable="130000")

    for amounts in (stray, unused, short):
        assert [(found.code, found.severity) for found in amounts.findings] == [("HEADER_LEVEL_ONLY", "block")]
        assert (amounts.level, amounts.parts, amounts.lines) == ("header", [], [])

    assert "is not on exactly one of them" in stray.findings[0].message
    assert "no line of the invoice is on PO-2024-0143" in unused.findings[0].message
    assert "add up to 121,000.00, not the taxable value 130,000.00" in short.findings[0].message
