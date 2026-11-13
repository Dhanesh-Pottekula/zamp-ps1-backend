"""Conversion of printed text into typed values, and extraction failures."""

from datetime import date
from decimal import Decimal

import pytest
from conftest import extraction, line

from app.adapters import AdapterRegistry
from app.entities import EntitiesRegistry
from app.entities.stages.extraction_stage import _Helper


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("1,70,000.00", Decimal("170000.00")),
        ("2,00,600.00", Decimal("200600.00")),
        ("36,000.00", Decimal("36000.00")),
        ("₹ 42,480.00", Decimal("42480.00")),
        ("Rs. 1,200", Decimal(1200)),
        ("INR 5,000", Decimal(5000)),
        ("(500.00)", Decimal("-500.00")),
        ("-1,000.50", Decimal("-1000.50")),
        ("18%", Decimal(18)),
        ("30 pcs", Decimal(30)),
        ("", None),
        ("N/A", None),
    ],
)
def test_amount_parsing(printed: str, expected: Decimal | None) -> None:
    assert _Helper.amount(printed) == expected


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("1.249,50", Decimal("1249.50")),
        ("14.994,00 €", Decimal("14994.00")),
        ("1 234,56", Decimal("1234.56")),
        ("2.848,86", Decimal("2848.86")),
        ("19 %", Decimal(19)),
        ("12", Decimal(12)),
        ("-1.000,00", Decimal("-1000.00")),
        ("1,234,56", None),
    ],
)
def test_decimal_comma_amounts(printed: str, expected: Decimal | None) -> None:
    assert _Helper.amount(printed, decimal_comma=True) == expected


def test_swiss_apostrophe_grouping() -> None:
    assert _Helper.amount("CHF 1'234.50") == Decimal("1234.50")


@pytest.mark.parametrize(
    ("model", "printed", "order", "expected", "ambiguous"),
    [
        ("2024-04-11", "Date: 11/04/2024", "MDY", date(2024, 11, 4), False),
        ("2024-11-04", "Date: 11/04/2024", "DMY", date(2024, 4, 11), False),
        ("2024-11-04", "Date: 11/04/2024", "", date(2024, 4, 11), True),
        ("2024-04-04", "Date: 04/04/2024", "", date(2024, 4, 4), False),
        ("2024-11-24", "Date: 11/24/2024", "", date(2024, 11, 24), False),
        ("2024-11-14", "Datum: 14.11.2024", "", date(2024, 11, 14), False),
        ("2024-10-25", "Date: 10/25/2024", "DMY", date(2024, 10, 25), False),
        ("2024-11-04", "Order date: 01/10/2024", "", date(2024, 11, 4), False),
        ("", "Date: 11/04/2024", "MDY", None, False),
    ],
)
def test_printed_date_settles_the_order(
    model: str, printed: str, order: str, expected: date | None, ambiguous: bool
) -> None:
    value, unclear = _Helper.dated("invoice_date", model, {"invoice_date": printed}, order)  # type: ignore[arg-type]
    assert (value, unclear) == (expected, ambiguous)


def test_model_date_is_used_without_a_numeric_quote() -> None:
    value, unclear = _Helper.dated("invoice_date", "2024-11-04", {"invoice_date": "Dated 4 November 2024"}, "")
    assert (value, unclear) == (date(2024, 11, 4), False)


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("2024-11-04", date(2024, 11, 4)),
        ("04/11/2024", date(2024, 11, 4)),
        ("04-11-2024", date(2024, 11, 4)),
        ("4 Nov 2024", date(2024, 11, 4)),
        ("12-Sep-2024", date(2024, 9, 12)),
        ("14.11.2024", date(2024, 11, 14)),
        ("Nov 4, 2024", date(2024, 11, 4)),
        ("November 4, 2024", date(2024, 11, 4)),
        ("31/02/2024", None),
        ("", None),
        ("next Tuesday", None),
    ],
)
def test_date_parsing(printed: str, expected: date | None) -> None:
    assert _Helper.day(printed, "") == expected


def test_conversion_normalises_blanks_and_indian_grouping() -> None:
    result = extraction(
        "INV-002", po_reference_text="  ", due_date="", currency="inr", bank_name="", lines=[line(unit_price="")]
    )

    assert result.po_reference_text is None
    assert result.due_date is None
    assert result.currency == "INR"
    assert result.supplier_bank.bank_name is None
    assert result.subtotal is None
    assert result.total == Decimal("200600.00")
    assert result.lines[0].unit_price is None
    assert result.lines[0].quantity == Decimal(30)
    assert result.invoice_date == date(2024, 11, 5)


async def test_unknown_document_fails_extraction_in_recorded_mode() -> None:
    adapter = AdapterRegistry()
    await EntitiesRegistry.initialize(adapter)
    try:
        result = await EntitiesRegistry.ExtractionStage.extract(b"%PDF-1.4", "application/pdf", "my-invoice.pdf")
    finally:
        await adapter.close()

    assert result.extraction is None
    assert result.mode == "recorded"
    assert [finding.code for finding in result.findings] == ["EXTRACTION_FAILED"]
    assert result.findings[0].severity == "block"
    assert "ANTHROPIC_API_KEY" in result.findings[0].message


@pytest.mark.parametrize(
    ("printed", "separator", "expected"),
    [
        ("36.000", "", None),
        ("1.250 €", "", None),
        ("36.000", ",", Decimal(36000)),
        ("1.250", ".", Decimal("1.250")),
        ("0.125", ".", Decimal("0.125")),
        ("23,600", "", Decimal(23600)),
    ],
)
def test_dot_grouped_whole_numbers_need_a_known_separator(
    printed: str, separator: str, expected: Decimal | None
) -> None:
    reading = _Helper.amount(printed, decimal_comma=separator == ",", dots_unknown=separator == "")
    assert reading == expected
