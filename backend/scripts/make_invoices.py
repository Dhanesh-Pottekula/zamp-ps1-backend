"""Build the demo invoice PDFs and their InvoiceRead answer keys.

Run from backend/: `uv run python scripts/make_invoices.py`.
Writes `data/invoices/INV-NNN.pdf` and `data/invoices/INV-NNN.read.json`; reruns produce identical bytes.
An empty spec number prints no invoice-number row; an inclusive spec prints tax-inclusive prices and a note;
a no_bank spec prints no bank details. The other switches make one document wrong in one way: a printed name or tax ID
that differs from the master ("" prints none), no address, another or no currency, no date, no total, printed sums
that do not add up, a document title (a credit note), no date order the reader can tell, or a password.
Each spec has a locale: IN (GST, Indian digit grouping, day-first dates), US (no sales tax on an export, month-first
dates), DE (German labels, 1.234,56 amounts, MwSt. 19 %, IBAN) or GB (VAT-inclusive prices, sort code).
"""

import csv
import io
import json
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal, TypedDict, cast

import pypdfium2
from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle


class _Types:
    type Layout = Literal["A", "B", "C"]
    type Finish = Literal["box", "band", "rule"]
    type Country = Literal["IN", "US", "DE", "GB"]

    @dataclass(frozen=True)
    class Locale:
        currency: str
        date_format: str
        date_order: str
        decimal: str
        indian_grouping: bool
        title: str
        tax_id_label: str
        bank_labels: tuple[str, str, str, str]
        header: tuple[str, ...]
        buyer: tuple[str, ...]
        buyer_name: str
        buyer_tax_id: str
        tax_name: str
        tax_rate: Decimal
        tax_row: str
        gross_note: str
        footer_a: str
        footer_b: str
        unit: str
        bill_to: str = "Bill To"
        bank_heading: str = "Bank Details"
        signed_for: str = "For"
        signatory: str = "Authorised Signatory"

    class VendorRow(TypedDict):
        vendor_id: str
        legal_name: str
        tax_id: str
        bank_account: str
        bank_code: str
        bank_name: str
        email_domain: str
        payment_terms: str

    class BankSwap(TypedDict):
        account_number: str
        bank_code: str
        bank_name: str

    class Labels(TypedDict):
        number: str
        issued: str
        due: str
        order: str
        subtotal: str
        total: str

    @dataclass(frozen=True)
    class LineSpec:
        description: str
        hsn: str
        quantity: int
        unit: str
        rate: Decimal
        show_rate: bool = True  # False prints the amount with no unit price (a lump-sum line)

    @dataclass(frozen=True)
    class InvoiceSpec:
        file_id: str
        vendor_id: str
        number: str
        issued: date
        po_ref: str
        lines: tuple[_Types.LineSpec, ...]
        layout: _Types.Layout
        scanned: bool = False
        bank_swap: _Types.BankSwap | None = None
        no_bank: bool = False
        inclusive: bool = False
        country: _Types.Country = "IN"
        printed_name: str = ""  # "" prints the master name
        printed_tax_id: str | None = None  # None prints the master tax ID, "" prints none
        no_address: bool = False  # no supplier address or email
        currency: str | None = None  # None: the locale's; "": none printed; else that code
        no_date: bool = False
        no_total: bool = False
        printed_subtotal: Decimal | None = None  # a subtotal that differs from the line sum
        printed_total: Decimal | None = None  # a total that differs from subtotal plus tax
        title: str = ""  # "" prints the locale's title
        unclear_dates: bool = False  # the reading reports no date order: nothing on the page tells
        encrypted: bool = False

    class LineRead(TypedDict):
        description: str
        quantity: str
        unit: str
        unit_price: str
        line_amount: str
        tax_rate_pct: str

    class TaxRead(TypedDict):
        label: str
        rate_pct: str
        amount: str

    class SourceRead(TypedDict):
        field: str
        text: str

    class InvoiceRead(TypedDict):
        supplier_name: str
        supplier_tax_id: str
        supplier_address: str
        supplier_email: str
        bank_account_name: str
        bank_account_number: str
        bank_code: str
        bank_name: str
        buyer_name: str
        buyer_tax_id: str
        invoice_number: str
        invoice_date: str
        due_date: str
        currency: str
        date_order: str
        decimal_separator: str
        po_reference_text: str
        prices_include_tax: bool
        lines: list[_Types.LineRead]
        subtotal: str
        tax_lines: list[_Types.TaxRead]
        total: str
        sources: list[_Types.SourceRead]

    @dataclass(frozen=True)
    class Sheet:
        spec: _Types.InvoiceSpec
        read: _Types.InvoiceRead
        address: tuple[str, ...]
        words: str


class _Helper:
    GROUPING: Final = re.compile(r"(\d)(?=(\d{2})*\d{3}$)")
    SMALL: Final = (
        "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
        "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen",
    )  # fmt: skip
    TENS: Final = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")
    ADDRESSES: Final[dict[str, tuple[str, ...]]] = {
        "V001": ("Plot 42, 2nd Phase, Peenya Industrial Area", "Bengaluru, Karnataka 560058"),
        "V002": ("Unit 7, Shah Industrial Estate, Andheri East", "Mumbai, Maharashtra 400069"),
        "V003": ("14 SIDCO Industrial Estate, Guindy", "Chennai, Tamil Nadu 600032"),
        "V004": ("B-112, Okhla Industrial Area Phase II", "New Delhi, Delhi 110020"),
        "V005": ("221, 6th Cross, Wilson Garden", "Bengaluru, Karnataka 560027"),
        "V006": ("27 Ambattur Industrial Estate", "Chennai, Tamil Nadu 600058"),
        "V007": ("2200 Commerce Parkway, Suite 400", "Columbus, OH 43219, USA"),
        "V008": ("Industriestraße 27", "70565 Stuttgart, Deutschland"),
        "V009": ("Unit 4, Wharf Road", "Bristol BS1 4RN, United Kingdom"),
        "V010": ("3rd Floor, Prestige Tech Park, Marathahalli", "Bengaluru, Karnataka 560103"),
        "V011": ("18, 2nd Main, Jayanagar 4th Block", "Bengaluru, Karnataka 560011"),
        "V012": ("44, Residency Road", "Bengaluru, Karnataka 560025"),
        "V013": ("27, Bannerghatta Road, JP Nagar", "Bengaluru, Karnataka 560078"),
        "X001": ("No. 9, Hosur Road, Bommanahalli", "Bengaluru, Karnataka 560068"),
    }
    OFF_MASTER: Final[dict[str, _Types.VendorRow]] = {
        "X001": {
            "vendor_id": "X001",
            "legal_name": "Lotus Workspace Interiors Pvt Ltd",
            "tax_id": "29AAFCL3456R1Z7",
            "bank_account": "50100098765432",
            "bank_code": "HDFC0004567",
            "bank_name": "HDFC Bank",
            "email_domain": "lotusinteriors.in",
            "payment_terms": "Net 30",
        }
    }
    LOCALES: Final[dict[str, _Types.Locale]] = {
        "IN": _Types.Locale(
            currency="INR",
            date_format="%d/%m/%Y",
            date_order="DMY",
            decimal=".",
            indian_grouping=True,
            title="TAX INVOICE",
            tax_id_label="GSTIN",
            bank_labels=("Account Name:", "Account Number:", "IFSC:", "Bank:"),
            header=("#", "Description", "HSN/SAC", "Qty", "Unit", "Rate", "GST %", "Amount"),
            buyer=(
                "No. 18, 3rd Floor, Residency Road",
                "Shanthala Nagar, Bengaluru, Karnataka 560025",
                "GSTIN: 29AAGCM4567H1Z9",
                "Place of Supply: Karnataka (29)",
            ),
            buyer_name="Meridian Retail Pvt Ltd",
            buyer_tax_id="29AAGCM4567H1Z9",
            tax_name="GST",
            tax_rate=Decimal(18),
            tax_row="{label} @ {rate}%",
            gross_note="All prices are inclusive of GST @ {rate}%",
            footer_a="This is a computer generated invoice.",
            footer_b="Subject to Bengaluru jurisdiction. Thank you for your business.",
            unit="pcs",
        ),
        "US": _Types.Locale(
            currency="USD",
            date_format="%m/%d/%Y",
            date_order="MDY",
            decimal=".",
            indian_grouping=False,
            title="COMMERCIAL INVOICE",
            tax_id_label="EIN",
            bank_labels=("Beneficiary:", "Account Number:", "SWIFT:", "Bank:"),
            header=("#", "Description", "Part No.", "Qty", "Unit", "Unit Price", "Tax %", "Amount"),
            buyer=(
                "No. 18, 3rd Floor, Residency Road",
                "Bengaluru, Karnataka 560025, India",
                "GSTIN: 29AAGCM4567H1Z9",
                "Export sale: no US sales tax charged",
            ),
            buyer_name="Meridian Retail Pvt Ltd",
            buyer_tax_id="29AAGCM4567H1Z9",
            tax_name="Sales tax",
            tax_rate=Decimal(0),
            tax_row="{label} @ {rate}%",
            gross_note="",
            footer_a="Payment by wire transfer in USD.",
            footer_b="Payment by wire transfer in USD.",
            unit="pcs",
        ),
        "DE": _Types.Locale(
            currency="EUR",
            date_format="%d.%m.%Y",
            date_order="DMY",
            decimal=",",
            indian_grouping=False,
            title="RECHNUNG",
            tax_id_label="USt-IdNr.",
            bank_labels=("Kontoinhaber:", "IBAN:", "BIC:", "Bank:"),
            header=("Pos.", "Bezeichnung", "Art.-Nr.", "Menge", "Einheit", "Einzelpreis", "MwSt.", "Betrag"),
            buyer=("Friedrichstraße 68", "10117 Berlin, Deutschland", "USt-IdNr.: DE312345678"),
            buyer_name="Meridian Retail Europe GmbH",
            buyer_tax_id="DE312345678",
            tax_name="MwSt.",
            tax_rate=Decimal(19),
            tax_row="{label} {rate} %",
            gross_note="",
            footer_a="Diese Rechnung wurde maschinell erstellt und ist ohne Unterschrift gültig.",
            footer_b="Zahlbar ohne Abzug innerhalb von 30 Tagen.",
            unit="Stk.",
            bill_to="Rechnungsempfänger",
            bank_heading="Bankverbindung",
            signed_for="Für",
            signatory="Unterschrift",
        ),
        "GB": _Types.Locale(
            currency="GBP",
            date_format="%d/%m/%Y",
            date_order="DMY",
            decimal=".",
            indian_grouping=False,
            title="VAT INVOICE",
            tax_id_label="VAT Reg. No.",
            bank_labels=("Account Name:", "Account Number:", "Sort Code:", "Bank:"),
            header=("#", "Description", "Code", "Qty", "Unit", "Price inc. VAT", "VAT %", "Amount"),
            buyer=("14 Hanover Square", "London W1S 1HP, United Kingdom", "VAT Reg. No.: GB298765421"),
            buyer_name="Meridian Retail UK Ltd",
            buyer_tax_id="GB298765421",
            tax_name="VAT",
            tax_rate=Decimal(20),
            tax_row="{label} @ {rate}% (included)",
            gross_note="All prices include VAT at {rate}%",
            footer_a="Thank you for your business.",
            footer_b="Registered in England and Wales. Thank you for your business.",
            unit="pcs",
        ),
    }
    LABELS: Final[dict[str, _Types.Labels]] = {
        "A": {
            "number": "Invoice No.:",
            "issued": "Invoice Date:",
            "due": "Due Date:",
            "order": "PO Reference:",
            "subtotal": "Taxable Value",
            "total": "Grand Total (INR)",
        },
        "B": {
            "number": "Invoice Number:",
            "issued": "Dated:",
            "due": "Payment Due:",
            "order": "Your Order No.:",
            "subtotal": "Taxable Value",
            "total": "Total Amount Payable (INR)",
        },
        "C": {
            "number": "Invoice #",
            "issued": "Date",
            "due": "Due",
            "order": "Order Ref.",
            "subtotal": "Taxable value",
            "total": "Total (INR)",
        },
    }
    FOREIGN_LABELS: Final[dict[str, _Types.Labels]] = {
        "US": {
            "number": "Invoice No.:",
            "issued": "Invoice Date:",
            "due": "Due Date:",
            "order": "PO Number:",
            "subtotal": "Subtotal",
            "total": "Total Due (USD)",
        },
        "DE": {
            "number": "Rechnungsnummer:",
            "issued": "Rechnungsdatum:",
            "due": "Fällig am:",
            "order": "Ihre Bestellung:",
            "subtotal": "Nettobetrag",
            "total": "Gesamtbetrag (EUR)",
        },
        "GB": {
            "number": "Invoice No.:",
            "issued": "Invoice Date:",
            "due": "Payment Due:",
            "order": "Your PO:",
            "subtotal": "Net amount",
            "total": "Total (GBP)",
        },
    }
    INK: Final = colors.HexColor("#1f2933")
    MUTED: Final = colors.HexColor("#52606d")
    ACCENT: Final = colors.HexColor("#0b4f6c")
    SHADE: Final = colors.HexColor("#e6eef2")
    LEFT: Final = 40.0
    RIGHT: Final = A4[0] - 40.0
    COLUMNS: Final = (22.0, 178.0, 52.0, 40.0, 34.0, 66.0, 40.0, 83.0)

    @staticmethod
    def invoice_specs() -> list[_Types.InvoiceSpec]:
        """Return the invoices of section 7 in file order."""
        stand = _Types.LineSpec("Laptop stand (adjustable aluminium)", "9403", 30, "pcs", Decimal(1200))
        first = _Types.InvoiceSpec(
            "INV-001", "V001", "ACM/24-25/0187", date(2024, 11, 4), "PO-2024-0142", (stand,), "A"
        )
        return [
            # Happy path first: the duplicate samples later re-use its invoice and its PO.
            first,
            # Stage 0, intake: a scanned PDF with tax-inclusive prices is read by vision; a locked one is refused.
            _Types.InvoiceSpec(
                "INV-002",
                "V002",
                "ZT-2024-1105",
                date(2024, 11, 5),
                "PO-2024-0143",
                (_Types.LineSpec("Ergonomic office chair", "9401", 20, "pcs", Decimal(10030)),),
                "B",
                scanned=True,
                inclusive=True,
            ),
            _Types.InvoiceSpec(
                "INV-003",
                "V001",
                "ACM/24-25/0244",
                date(2024, 11, 26),
                "PO-2024-0158",
                (_Types.LineSpec("Cable management tray (steel)", "7326", 10, "pcs", Decimal(650)),),
                "A",
                encrypted=True,
            ),
            # Stage 2, validation: header fields and arithmetic.
            _Types.InvoiceSpec(
                "INV-004",
                "V001",
                "",
                date(2024, 11, 21),
                "PO-2024-0153",
                (_Types.LineSpec("Wireless keyboard and mouse combo", "8471", 25, "pcs", Decimal(1800)),),
                "A",
                printed_subtotal=Decimal(45500),
                printed_total=Decimal(54000),
            ),
            _Types.InvoiceSpec(
                "INV-006",
                "V011",
                "SFS/30-31/0004",
                date(2030, 4, 3),
                "PO-2024-0163",
                (_Types.LineSpec("Paper shredder (cross-cut)", "8472", 8, "pcs", Decimal(9500)),),
                "A",
                printed_tax_id="",
                no_address=True,
                currency="",
                unclear_dates=True,
            ),
            _Types.InvoiceSpec(
                "INV-008",
                "V001",
                "ACM/CN/24-25/009",
                date(2024, 11, 26),
                "PO-2024-0158",
                (_Types.LineSpec("Cable management tray (steel)", "7326", -5, "pcs", Decimal(650)),),
                "A",
                title="CREDIT NOTE",
                no_date=True,
                no_total=True,
            ),
            # Other countries' number, date and tax formats.
            _Types.InvoiceSpec(
                "INV-010",
                "V007",
                "GP-10442",
                date(2024, 11, 4),
                "PO-2024-0150",
                (_Types.LineSpec("Precision bearing 6204-2RS", "BRG-6204", 100, "pcs", Decimal(45)),),
                "C",
                country="US",
            ),
            _Types.InvoiceSpec(
                "INV-011",
                "V008",
                "KI-2024-0418",
                date(2024, 11, 14),
                "PO-2024-0154",
                (_Types.LineSpec("Torque wrench set 1/2 in.", "DS-1250", 12, "Stk.", Decimal("1249.50")),),
                "A",
                country="DE",
            ),
            _Types.InvoiceSpec(
                "INV-012",
                "V009",
                "HF-3317",
                date(2024, 11, 5),
                "PO-2024-0155",
                (_Types.LineSpec("Oak display shelving unit", "SHV-OAK", 8, "pcs", Decimal(495)),),
                "B",
                inclusive=True,
                country="GB",
            ),
            # Stage 3, vendor: who is invoicing.
            _Types.InvoiceSpec(
                "INV-013",
                "X001",
                "LWS/2024/117",
                date(2024, 11, 26),
                "",
                (_Types.LineSpec("Workstation partition panel", "9403", 12, "pcs", Decimal(4800)),),
                "A",
            ),
            _Types.InvoiceSpec(
                "INV-014",
                "V006",
                "NCS/24-25/0312",
                date(2024, 12, 10),
                "",
                (_Types.LineSpec("Proximity sensor M12 (PNP)", "8536", 50, "pcs", Decimal(640)),),
                "C",
            ),
            _Types.InvoiceSpec(
                "INV-015",
                "V001",
                "ACM/24-25/0240",
                date(2024, 11, 24),
                "PO-2024-0153",
                (_Types.LineSpec("Wireless keyboard and mouse combo", "8471", 10, "pcs", Decimal(1800)),),
                "B",
                printed_tax_id="29AACCA1234F0Z5",
            ),
            _Types.InvoiceSpec(
                "INV-016",
                "V002",
                "ZOS/24-25/118",
                date(2024, 11, 16),
                "PO-2024-0156",
                (_Types.LineSpec("Ergonomic office chair", "9401", 5, "pcs", Decimal(8500)),),
                "C",
                printed_name="ZT Office Solutions",
                printed_tax_id="",
            ),
            _Types.InvoiceSpec(
                "INV-017",
                "V004",
                "OL-INV-7840",
                date(2024, 11, 18),
                "PO-2024-0145",
                (_Types.LineSpec("Freight - Bengaluru to Mumbai (October)", "996511", 1, "lot", Decimal(80000)),),
                "A",
                printed_tax_id="",
                no_bank=True,
            ),
            _Types.InvoiceSpec(
                "INV-018",
                "V003",
                "NC/1124/077",
                date(2024, 11, 20),
                "",
                (_Types.LineSpec("Proximity sensor M12 (PNP)", "8536", 20, "pcs", Decimal(640)),),
                "C",
                printed_name="Nova Component Pvt Ltd",
                printed_tax_id="",
                no_bank=True,
            ),
            # Stage 4, bank: where the money goes.
            _Types.InvoiceSpec(
                "INV-019",
                "V005",
                "PP/2024/0456",
                date(2024, 11, 11),
                "PO-2024-0147",
                (_Types.LineSpec("Product brochure (A4 colour)", "4911", 5000, "pcs", Decimal(5)),),
                "B",
                bank_swap={"account_number": "77700011122", "bank_code": "YESB0000246", "bank_name": "Yes Bank"},
            ),
            # Stage 5, duplicates: INV-001 re-sent exactly, then re-issued under a new number.
            _Types.InvoiceSpec("INV-021", first.vendor_id, first.number, first.issued, first.po_ref, first.lines, "B"),
            _Types.InvoiceSpec(
                "INV-023", first.vendor_id, "ACM/24-25/0236", first.issued, first.po_ref, first.lines, "B"
            ),
            # Stage 6, PO matching: no PO number printed, then with the PO number printed.
            _Types.InvoiceSpec(
                "INV-027",
                "V010",
                "BLI/24-25/0425",
                date(2024, 11, 29),
                "",
                (_Types.LineSpec("Monitor (24 inch IPS)", "8528", 10, "pcs", Decimal(11000)),),
                "C",
            ),
            _Types.InvoiceSpec(
                "INV-028",
                "V010",
                "BLI/24-25/0431",
                date(2024, 11, 30),
                "PO-2024-0159 / PO-2024-0160",
                (
                    _Types.LineSpec("Wireless headset", "8518", 20, "pcs", Decimal(3200)),
                    _Types.LineSpec("Height-adjustable office desk", "9403", 15, "pcs", Decimal(9000)),
                ),
                "A",
            ),
            _Types.InvoiceSpec(
                "INV-029",
                "V004",
                "OL-INV-7802",
                date(2024, 11, 23),
                "PO-2024-0153",
                (_Types.LineSpec("Freight - Bengaluru to Chennai (November)", "996511", 1, "lot", Decimal(45000)),),
                "C",
            ),
            _Types.InvoiceSpec(
                "INV-030",
                "V005",
                "PPS/24-25/0398",
                date(2024, 12, 11),
                "PO-2024-0149",
                (_Types.LineSpec("Business cards (box of 100)", "4911", 2000, "box", Decimal("2.50")),),
                "A",
            ),
            # No PO number printed: the vendor's open POs are searched by line.
            _Types.InvoiceSpec(
                "INV-033",
                "V011",
                "SFS/24-25/0071",
                date(2024, 12, 2),
                "",
                (
                    _Types.LineSpec("Ergonomic mesh task chair", "9401", 10, "pcs", Decimal(7500)),
                    _Types.LineSpec("LED desk lamp with dimmer", "9405", 30, "pcs", Decimal(1800)),
                    _Types.LineSpec("Laser printer (A4 mono)", "8443", 2, "pcs", Decimal(18000)),
                ),
                "B",
            ),
            # Jev decides every invoice line the code is not sure about.
            _Types.InvoiceSpec(
                "INV-034",
                "V012",
                "KOI/24-25/0210",
                date(2024, 12, 12),
                "PO-2024-0165",
                (
                    _Types.LineSpec("Ergo footrest pad", "9403", 10, "pcs", Decimal(1400)),
                    _Types.LineSpec("Glass marker board 6ft x 4ft", "9610", 2, "pcs", Decimal(12500)),
                ),
                "B",
            ),
            _Types.InvoiceSpec(
                "INV-035",
                "V012",
                "KOI/24-25/0214",
                date(2024, 12, 13),
                "PO-2024-0166",
                (_Types.LineSpec("Meeting table wire tray", "7326", 12, "pcs", Decimal(900)),),
                "C",
            ),
            _Types.InvoiceSpec(
                "INV-037",
                "V013",
                "VWS/24-25/0102",
                date(2024, 12, 18),
                "",
                (
                    _Types.LineSpec("Wireless headset", "8518", 10, "pcs", Decimal(3200)),
                    _Types.LineSpec("Monitor", "8528", 5, "pcs", Decimal(2450)),
                    _Types.LineSpec("Freight & handling", "996511", 1, "lot", Decimal(1500)),
                ),
                "A",
            ),
            _Types.InvoiceSpec(
                "INV-038",
                "V012",
                "KOI/24-25/0219",
                date(2024, 12, 14),
                "",
                (_Types.LineSpec("Stackable visitor chair", "9401", 30, "pcs", Decimal(2600)),),
                "A",
            ),
            _Types.InvoiceSpec(
                "INV-039",
                "V012",
                "KOI/24-25/0225",
                date(2024, 12, 16),
                "",
                (_Types.LineSpec("Stackable visitor chair", "9401", 70, "pcs", Decimal(2600)),),
                "B",
            ),
            _Types.InvoiceSpec(
                "INV-040",
                "V013",
                "VWS/24-25/0109",
                date(2024, 12, 20),
                "",
                (_Types.LineSpec("Cafeteria chair (polypropylene)", "9401", 30, "pcs", Decimal(2800)),),
                "B",
            ),
            # Stage 7, amounts: the code checks money and quantities on the matched lines.
            _Types.InvoiceSpec(
                "INV-043",
                "V005",
                "PP/2024/0502",
                date(2024, 11, 24),
                "PO-2024-0157",
                (_Types.LineSpec("Spiral notebook (A5, ruled)", "4820", 400, "pcs", Decimal(66)),),
                "B",
            ),
            _Types.InvoiceSpec(
                "INV-044",
                "V011",
                "SFS/24-25/0086",
                date(2024, 12, 6),
                "",
                (
                    _Types.LineSpec("Ergonomic mesh task chair", "9401", 14, "pcs", Decimal(7500)),
                    _Types.LineSpec("Microwave oven (25 litre)", "8516", 2, "pcs", Decimal(8500)),
                ),
                "C",
            ),
            _Types.InvoiceSpec(
                "INV-045",
                "V011",
                "SFS/24-25/0097",
                date(2024, 12, 12),
                "PO-2024-0164",
                (_Types.LineSpec("Water dispenser (hot and cold)", "8516", 2, "pcs", Decimal(14000)),),
                "C",
                currency="USD",
            ),
            _Types.InvoiceSpec(
                "INV-046",
                "V011",
                "SFS/24-25/0102",
                date(2024, 12, 13),
                "PO-2024-0162 / PO-2024-0164",
                (
                    _Types.LineSpec("Acoustic partition panel", "9403", 4, "pcs", Decimal(6200), show_rate=False),
                    _Types.LineSpec("Water dispenser (hot and cold)", "8516", 2, "pcs", Decimal(14000)),
                ),
                "B",
            ),
        ]

    @staticmethod
    def load_vendors(csv_path: Path) -> dict[str, _Types.VendorRow]:
        """Read vendors.csv into rows keyed by vendor_id."""
        with csv_path.open(newline="", encoding="utf-8") as handle:
            return {row["vendor_id"]: cast("_Types.VendorRow", row) for row in csv.DictReader(handle)}

    @staticmethod
    def money(amount: Decimal, country: str = "IN") -> str:
        """Format an amount the way the country prints it: 1,70,000.00 (IN), 170,000.00 (US, GB), 170.000,00 (DE)."""
        locale = _Helper.LOCALES[country]
        if locale.indian_grouping:
            whole, fraction = f"{amount:.2f}".split(".")
            return f"{_Helper.GROUPING.sub(r'\1,', whole)}.{fraction}"

        western = f"{amount:,.2f}"
        return western.translate(str.maketrans(",.", ".,")) if locale.decimal == "," else western

    @staticmethod
    def labels(spec: _Types.InvoiceSpec) -> _Types.Labels:
        """Return the printed labels: the layout's own for an Indian invoice, the country's otherwise."""
        labels = _Helper.LABELS[spec.layout] if spec.country == "IN" else _Helper.FOREIGN_LABELS[spec.country]
        if spec.currency is None:
            return labels

        native = f" ({_Helper.LOCALES[spec.country].currency})"
        shown = f" ({spec.currency})" if spec.currency else ""
        return cast("_Types.Labels", {**labels, "total": labels["total"].replace(native, shown)})

    @staticmethod
    def title(sheet: _Types.Sheet) -> str:
        """Return the document title: the spec's own (a credit note) or the locale's."""
        return sheet.spec.title or _Helper.LOCALES[sheet.spec.country].title

    @staticmethod
    def spell(number: int) -> str:
        """Spell a non-negative integer in the Indian system (crore, lakh, thousand)."""
        if number < 20:
            return _Helper.SMALL[number]

        if number < 100:
            return f"{_Helper.TENS[number // 10]} {_Helper.SMALL[number % 10]}".strip()

        if number < 1000:
            return f"{_Helper.SMALL[number // 100]} Hundred {_Helper.spell(number % 100)}".strip()

        scales = ((10**7, "Crore"), (10**5, "Lakh"), (10**3, "Thousand"))
        size, name = next(scale for scale in scales if number >= scale[0])
        return f"{_Helper.spell(number // size)} {name} {_Helper.spell(number % size)}".strip()

    @staticmethod
    def build_sheet(spec: _Types.InvoiceSpec, vendor: _Types.VendorRow) -> _Types.Sheet:
        """Derive every printed value of one invoice as the InvoiceRead a careful reader would produce."""
        locale = _Helper.LOCALES[spec.country]
        labels = _Helper.labels(spec)
        address = () if spec.no_address else _Helper.ADDRESSES[spec.vendor_id]
        name = spec.printed_name or vendor["legal_name"]
        tax_id = vendor["tax_id"] if spec.printed_tax_id is None else spec.printed_tax_id
        currency = locale.currency if spec.currency is None else spec.currency
        line_sum = sum((line.rate * line.quantity for line in spec.lines), Decimal(0))
        due = spec.issued + timedelta(days=int(vendor["payment_terms"].removeprefix("Net ")))
        bank = spec.bank_swap or {
            "account_number": vendor["bank_account"],
            "bank_code": vendor["bank_code"],
            "bank_name": vendor["bank_name"],
        }

        def money(amount: Decimal) -> str:
            return _Helper.money(amount, spec.country)

        taxes, total = _Helper.taxes(spec, locale, line_sum, intra=vendor["tax_id"].startswith("29"))
        subtotal = line_sum if spec.printed_subtotal is None else spec.printed_subtotal
        total = total if spec.printed_total is None else spec.printed_total
        printed_date = spec.issued.strftime(locale.date_format)
        printed_due = due.strftime(locale.date_format)
        quotes = (
            ("invoice_number", f"{labels['number']} {spec.number}", bool(spec.number)),
            ("invoice_date", f"{labels['issued']} {printed_date}", not spec.no_date),
            ("due_date", f"{labels['due']} {printed_due}", not spec.no_date),
            ("subtotal", f"{labels['subtotal']} {money(subtotal)}", not spec.inclusive),
            ("total", f"{labels['total']} {money(total)}", not spec.no_total),
            ("po_reference_text", f"{labels['order']} {spec.po_ref}", bool(spec.po_ref)),
        )
        sources: list[_Types.SourceRead] = [{"field": field, "text": text} for field, text, shown in quotes if shown]

        read: _Types.InvoiceRead = {
            "supplier_name": name,
            "supplier_tax_id": tax_id,
            "supplier_address": ", ".join(address),
            "supplier_email": "" if spec.no_address else f"accounts@{vendor['email_domain']}",
            "bank_account_name": "" if spec.no_bank else name,
            "bank_account_number": ""
            if spec.no_bank
            else _Helper.printed_account(bank["account_number"], spec.country),
            "bank_code": "" if spec.no_bank else _Helper.printed_code(bank["bank_code"], spec.country),
            "bank_name": "" if spec.no_bank else bank["bank_name"],
            "buyer_name": locale.buyer_name,
            "buyer_tax_id": locale.buyer_tax_id,
            "invoice_number": spec.number,
            "invoice_date": "" if spec.no_date else spec.issued.isoformat(),
            "due_date": "" if spec.no_date else due.isoformat(),
            "currency": currency,
            "date_order": "" if spec.unclear_dates else locale.date_order,
            "decimal_separator": locale.decimal,
            "po_reference_text": spec.po_ref,
            "prices_include_tax": spec.inclusive,
            "lines": [
                {
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit": line.unit,
                    "unit_price": money(line.rate) if line.show_rate else "",
                    "line_amount": money(line.rate * line.quantity),
                    "tax_rate_pct": f"{locale.tax_rate:f}",
                }
                for line in spec.lines
            ],
            "subtotal": "" if spec.inclusive else money(subtotal),
            "tax_lines": taxes,
            "total": "" if spec.no_total else money(total),
            "sources": sources,
        }
        rupees = spec.country == "IN" and currency == "INR" and not spec.no_total
        sign = "Minus " if total < 0 else ""
        words = f"Indian Rupees {sign}{_Helper.spell(int(abs(total)))} Only" if rupees else ""
        return _Types.Sheet(spec, read, address, words)

    @staticmethod
    def taxes(
        spec: _Types.InvoiceSpec, locale: _Types.Locale, line_sum: Decimal, *, intra: bool
    ) -> tuple[list[_Types.TaxRead], Decimal]:
        """
        Return the printed tax lines and the grand total.

        India: CGST + SGST inside Karnataka, IGST otherwise, and no tax lines on a GST-inclusive invoice. Elsewhere:
        the country's single rate on top of the lines, the VAT contained in gross prices shown as an included line, or
        nothing at a zero rate (a US export).
        """

        def money(amount: Decimal) -> str:
            return _Helper.money(amount, spec.country)

        cent = Decimal("0.01")
        if spec.country == "IN":
            half = (line_sum * Decimal("0.09")).quantize(cent)
            full = (line_sum * Decimal("0.18")).quantize(cent)
            taxes: list[_Types.TaxRead] = (
                [
                    {"label": "CGST", "rate_pct": "9", "amount": money(half)},
                    {"label": "SGST", "rate_pct": "9", "amount": money(half)},
                ]
                if intra
                else [{"label": "IGST", "rate_pct": "18", "amount": money(full)}]
            )
            return ([], line_sum) if spec.inclusive else (taxes, line_sum + (half * 2 if intra else full))

        rate = locale.tax_rate
        if rate == 0:
            return [], line_sum

        if spec.inclusive:
            contained = (line_sum - line_sum / (1 + rate / 100)).quantize(cent)
            return [{"label": locale.tax_name, "rate_pct": f"{rate:f}", "amount": money(contained)}], line_sum

        added = (line_sum * rate / 100).quantize(cent)
        return [{"label": locale.tax_name, "rate_pct": f"{rate:f}", "amount": money(added)}], line_sum + added

    @staticmethod
    def printed_account(account: str, country: str) -> str:
        """Print an IBAN in groups of four, as German invoices do; other account numbers as stored."""
        return (
            " ".join(account[index : index + 4] for index in range(0, len(account), 4)) if country == "DE" else account
        )

    @staticmethod
    def printed_code(code: str, country: str) -> str:
        """Print a UK sort code as 20-45-77; other bank codes as stored."""
        return "-".join(code[index : index + 2] for index in range(0, 6, 2)) if country == "GB" else code

    @staticmethod
    def printed(iso_date: str, country: str) -> str:
        """Turn an ISO date into the form printed on the country's invoices."""
        return date.fromisoformat(iso_date).strftime(_Helper.LOCALES[country].date_format)

    @staticmethod
    def place(canvas: Canvas, table: Table, left: float, top: float) -> float:
        """Draw a flowable table with its top-left corner at (left, top) and return its bottom y."""
        _, height = table.wrapOn(canvas, _Helper.RIGHT - left, top)
        table.drawOn(canvas, left, top - height)
        return top - height

    @staticmethod
    def item_table(sheet: _Types.Sheet, style: list[tuple]) -> Table:
        """Build the line-item table with the layout's style commands applied."""
        header = list(_Helper.LOCALES[sheet.spec.country].header)
        rows = [
            [str(index), read["description"], spec.hsn, read["quantity"], read["unit"], read["unit_price"],
             read["tax_rate_pct"], read["line_amount"]]
            for index, (read, spec) in enumerate(zip(sheet.read["lines"], sheet.spec.lines, strict=True), start=1)
        ]  # fmt: skip
        table = Table([header, *rows], colWidths=list(_Helper.COLUMNS))
        base = [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
            ("TEXTCOLOR", (0, 0), (-1, -1), _Helper.INK),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (4, 0), (4, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        table.setStyle(TableStyle(base + style))
        return table

    @staticmethod
    def write_totals(canvas: Canvas, sheet: _Types.Sheet, top: float, *, finish: _Types.Finish) -> float:
        """Draw the printed taxable value, tax lines, grand total and any GST-inclusive note; return the y below."""
        labels = _Helper.labels(sheet.spec)
        locale = _Helper.LOCALES[sheet.spec.country]
        rows = [
            *([(labels["subtotal"], sheet.read["subtotal"])] if sheet.read["subtotal"] else []),
            *(
                (locale.tax_row.format(label=tax["label"], rate=tax["rate_pct"]), tax["amount"])
                for tax in sheet.read["tax_lines"]
            ),
            *([(labels["total"], sheet.read["total"])] if sheet.read["total"] else []),
        ]
        left = _Helper.RIGHT - 240
        bottom = top - 18 * len(rows)
        divider = bottom + 18
        # The last row is styled as the grand total only when one is printed; without it no row may look like one.
        total_shown = bool(sheet.read["total"])

        canvas.setStrokeColor(_Helper.ACCENT if finish == "band" else _Helper.MUTED)
        canvas.setLineWidth(0.6)
        if finish == "band" and total_shown:
            canvas.setFillColor(_Helper.SHADE)
            canvas.rect(left, bottom, 240, 18, stroke=0, fill=1)

        if finish == "box":
            canvas.rect(left, bottom, 240, top - bottom, stroke=1, fill=0)

        if total_shown:
            canvas.line(left, divider, _Helper.RIGHT, divider)

        canvas.setFillColor(_Helper.INK)
        for offset, (label, amount) in enumerate(rows):
            font = "Helvetica-Bold" if total_shown and offset == len(rows) - 1 else "Helvetica"
            baseline = top - 18 * offset - 12.5
            pair = canvas.beginText(left + 6, baseline)
            pair.setFont(font, 9.5)
            pair.textOut(label)
            pair.setTextOrigin(_Helper.RIGHT - 6 - stringWidth(amount, font, 9.5), baseline)
            pair.textOut(amount)
            canvas.drawText(pair)

        if sheet.read["prices_include_tax"]:
            canvas.setFont("Helvetica-Oblique", 9)
            rate = sheet.read["lines"][0]["tax_rate_pct"]
            canvas.drawString(_Helper.LEFT, top - 12.5, locale.gross_note.format(rate=rate))

        return bottom

    @staticmethod
    def bank_rows(sheet: _Types.Sheet) -> list[str]:
        """Return the printed bank-detail lines."""
        read = sheet.read
        names = _Helper.LOCALES[sheet.spec.country].bank_labels
        values = (read["bank_account_name"], read["bank_account_number"], read["bank_code"], read["bank_name"])
        return [f"{name} {value}" for name, value in zip(names, values, strict=True)]

    @staticmethod
    def meta_rows(sheet: _Types.Sheet) -> list[str]:
        """Return the invoice number, date, due date and order reference lines, skipping an absent number or order."""
        labels = _Helper.labels(sheet.spec)
        read = sheet.read
        country = sheet.spec.country
        rows = [
            *([f"{labels['number']} {read['invoice_number']}"] if read["invoice_number"] else []),
            *([f"{labels['issued']} {_Helper.printed(read['invoice_date'], country)}"] if read["invoice_date"] else []),
            *([f"{labels['due']} {_Helper.printed(read['due_date'], country)}"] if read["due_date"] else []),
        ]
        if read["po_reference_text"]:
            rows.append(f"{labels['order']} {read['po_reference_text']}")

        return rows

    @staticmethod
    def write_lines(canvas: Canvas, lines: list[str], left: float, top: float, *, step: float = 12.5) -> float:
        """Draw left-aligned lines downward from top and return the y below the last line."""
        for offset, text in enumerate(lines):
            canvas.drawString(left, top - offset * step, text)

        return top - len(lines) * step

    @staticmethod
    def write_words(canvas: Canvas, sheet: _Types.Sheet, top: float) -> float:
        """Draw the amount in words, wrapped to the page width, and return the y below it."""
        if not sheet.words:
            return top + 22

        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(_Helper.LEFT, top, "Amount in words:")
        canvas.setFont("Helvetica", 9)
        wrapped = simpleSplit(sheet.words, "Helvetica", 9, _Helper.RIGHT - _Helper.LEFT - 80)
        return _Helper.write_lines(canvas, wrapped, _Helper.LEFT + 80, top)

    @staticmethod
    def write_signature(canvas: Canvas, sheet: _Types.Sheet, top: float) -> None:
        """Draw the authorised-signatory block on the right."""
        canvas.setFont("Helvetica-Bold", 9)
        locale = _Helper.LOCALES[sheet.spec.country]
        canvas.drawRightString(_Helper.RIGHT, top, f"{locale.signed_for} {sheet.read['supplier_name']}")
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(_Helper.RIGHT, top - 48, locale.signatory)

    @staticmethod
    def buyer_lines(sheet: _Types.Sheet) -> list[str]:
        """Return the buyer block body lines."""
        return [sheet.read["buyer_name"], *_Helper.LOCALES[sheet.spec.country].buyer]

    @staticmethod
    def draw_letterhead_left(canvas: Canvas, sheet: _Types.Sheet) -> None:
        """Layout A: supplier letterhead on the left, invoice details on the right, full grid table."""
        read = sheet.read
        canvas.setFillColor(_Helper.ACCENT)
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(_Helper.LEFT, 795, read["supplier_name"])
        canvas.setFillColor(_Helper.INK)
        canvas.setFont("Helvetica", 9)
        locale = _Helper.LOCALES[sheet.spec.country]
        supplier = [
            *sheet.address,
            *([f"{locale.tax_id_label}: {read['supplier_tax_id']}"] if read["supplier_tax_id"] else []),
            *([f"Email: {read['supplier_email']}"] if read["supplier_email"] else []),
        ]
        _Helper.write_lines(canvas, supplier, _Helper.LEFT, 778)

        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawRightString(_Helper.RIGHT, 795, _Helper.title(sheet))
        canvas.setFont("Helvetica", 9)
        _Helper.write_lines(canvas, _Helper.meta_rows(sheet), 375, 778)

        canvas.setStrokeColor(_Helper.ACCENT)
        canvas.setLineWidth(1.2)
        canvas.line(_Helper.LEFT, 715, _Helper.RIGHT, 715)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(_Helper.LEFT, 698, locale.bill_to)
        canvas.setFont("Helvetica", 9)
        bottom = _Helper.write_lines(canvas, _Helper.buyer_lines(sheet), _Helper.LEFT, 684)

        grid = [
            ("GRID", (0, 0), (-1, -1), 0.6, _Helper.MUTED),
            ("BACKGROUND", (0, 0), (-1, 0), _Helper.SHADE),
        ]
        bottom = _Helper.place(canvas, _Helper.item_table(sheet, grid), _Helper.LEFT, bottom - 14)
        bottom = _Helper.write_totals(canvas, sheet, bottom - 10, finish="box")
        bottom = _Helper.write_words(canvas, sheet, bottom - 22)

        if not sheet.spec.no_bank:
            canvas.setFont("Helvetica-Bold", 10)
            canvas.drawString(_Helper.LEFT, bottom - 20, locale.bank_heading)
            canvas.setFont("Helvetica", 9)
            _Helper.write_lines(canvas, _Helper.bank_rows(sheet), _Helper.LEFT, bottom - 35)

        _Helper.write_signature(canvas, sheet, bottom - 20)

        canvas.setFont("Helvetica-Oblique", 8)
        canvas.setFillColor(_Helper.MUTED)
        canvas.drawCentredString(A4[0] / 2, 40, locale.footer_a)

    @staticmethod
    def draw_letterhead_centred(canvas: Canvas, sheet: _Types.Sheet) -> None:
        """Layout B: centred letterhead over a banner, boxed party blocks, shaded table header."""
        read = sheet.read
        locale = _Helper.LOCALES[sheet.spec.country]
        middle = A4[0] / 2
        canvas.setFillColor(_Helper.ACCENT)
        canvas.rect(0, A4[1] - 14, A4[0], 14, stroke=0, fill=1)
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawCentredString(middle, 795, read["supplier_name"].upper())
        canvas.setFillColor(_Helper.INK)
        canvas.setFont("Helvetica", 9)
        canvas.drawCentredString(middle, 779, read["supplier_address"])
        parts = [
            *([f"{locale.tax_id_label}: {read['supplier_tax_id']}"] if read["supplier_tax_id"] else []),
            *([f"Email: {read['supplier_email']}"] if read["supplier_email"] else []),
        ]
        canvas.drawCentredString(middle, 766, "  |  ".join(parts))

        canvas.setStrokeColor(_Helper.ACCENT)
        canvas.setLineWidth(0.8)
        canvas.line(_Helper.LEFT, 752, _Helper.RIGHT, 752)
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawCentredString(middle, 730, _Helper.title(sheet))
        canvas.line(_Helper.LEFT, 720, _Helper.RIGHT, 720)

        canvas.setStrokeColor(_Helper.MUTED)
        canvas.rect(_Helper.LEFT, 618, 255, 92, stroke=1, fill=0)
        canvas.rect(_Helper.LEFT + 260, 618, 255, 92, stroke=1, fill=0)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(_Helper.LEFT + 8, 698, "Billed To")
        canvas.drawString(_Helper.LEFT + 268, 698, "Invoice Details")
        canvas.setFont("Helvetica", 9)
        _Helper.write_lines(canvas, _Helper.buyer_lines(sheet), _Helper.LEFT + 8, 684, step=12)
        _Helper.write_lines(canvas, _Helper.meta_rows(sheet), _Helper.LEFT + 268, 684, step=12)

        shaded = [
            ("BACKGROUND", (0, 0), (-1, 0), _Helper.ACCENT),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, _Helper.MUTED),
            ("BOX", (0, 0), (-1, -1), 0.6, _Helper.ACCENT),
        ]
        bottom = _Helper.place(canvas, _Helper.item_table(sheet, shaded), _Helper.LEFT, 600)
        bottom = _Helper.write_totals(canvas, sheet, bottom - 10, finish="band")
        bottom = _Helper.write_words(canvas, sheet, bottom - 22)

        if not sheet.spec.no_bank:
            canvas.setStrokeColor(_Helper.MUTED)
            canvas.rect(_Helper.LEFT, bottom - 80, 255, 68, stroke=1, fill=0)
            canvas.setFont("Helvetica-Bold", 9)
            canvas.drawString(_Helper.LEFT + 8, bottom - 25, "Bank Details for Payment")
            canvas.setFont("Helvetica", 9)
            _Helper.write_lines(canvas, _Helper.bank_rows(sheet), _Helper.LEFT + 8, bottom - 38, step=11)

        _Helper.write_signature(canvas, sheet, bottom - 25)

        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(_Helper.MUTED)
        canvas.drawCentredString(middle, 40, locale.footer_b)

    @staticmethod
    def draw_minimal(canvas: Canvas, sheet: _Types.Sheet) -> None:
        """Layout C: plain text, supplier on the right, rules instead of a grid."""
        read = sheet.read
        locale = _Helper.LOCALES[sheet.spec.country]
        canvas.setFillColor(_Helper.INK)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(_Helper.LEFT, 795, _Helper.title(sheet))
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawRightString(_Helper.RIGHT, 795, read["supplier_name"])
        canvas.setFont("Helvetica", 9)
        supplier = [
            *sheet.address,
            *([f"{locale.tax_id_label} {read['supplier_tax_id']}"] if read["supplier_tax_id"] else []),
            *([read["supplier_email"]] if read["supplier_email"] else []),
        ]
        for offset, text in enumerate(supplier):
            canvas.drawRightString(_Helper.RIGHT, 780 - offset * 12, text)

        _Helper.write_lines(canvas, _Helper.meta_rows(sheet), _Helper.LEFT, 760, step=13)

        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(_Helper.LEFT, 700, "Bill to")
        canvas.setFont("Helvetica", 9)
        bottom = _Helper.write_lines(canvas, _Helper.buyer_lines(sheet), _Helper.LEFT, 687, step=12)

        ruled = [
            ("LINEABOVE", (0, 0), (-1, 0), 0.8, _Helper.INK),
            ("LINEBELOW", (0, 0), (-1, 0), 0.4, _Helper.INK),
            ("LINEBELOW", (0, -1), (-1, -1), 0.8, _Helper.INK),
        ]
        bottom = _Helper.place(canvas, _Helper.item_table(sheet, ruled), _Helper.LEFT, bottom - 14)
        bottom = _Helper.write_totals(canvas, sheet, bottom - 8, finish="rule")
        bottom = _Helper.write_words(canvas, sheet, bottom - 22)

        if not sheet.spec.no_bank:
            canvas.setFont("Helvetica-Bold", 9)
            canvas.drawString(_Helper.LEFT, bottom - 20, "Payment details")
            canvas.setFont("Helvetica", 9)
            _Helper.write_lines(canvas, _Helper.bank_rows(sheet), _Helper.LEFT, bottom - 34, step=12)

    @staticmethod
    def render_pdf(sheet: _Types.Sheet) -> bytes:
        """Draw one invoice in its layout and return the PDF bytes."""
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=A4, invariant=1)
        canvas.setTitle(f"Tax Invoice {sheet.read['invoice_number']}".strip())
        canvas.setAuthor(sheet.read["supplier_name"])
        match sheet.spec.layout:
            case "A":
                _Helper.draw_letterhead_left(canvas, sheet)
            case "B":
                _Helper.draw_letterhead_centred(canvas, sheet)
            case "C":
                _Helper.draw_minimal(canvas, sheet)

        canvas.showPage()
        canvas.save()
        return buffer.getvalue()

    @staticmethod
    def scan(pdf_bytes: bytes) -> bytes:
        """Rasterise the first page into a slightly skewed, noisy JPEG and wrap it in an image-only PDF."""
        document = pypdfium2.PdfDocument(pdf_bytes)
        page = document[0]
        clean = page.render(scale=170 / 72).to_pil().convert("L")
        page.close()
        document.close()

        tilted = clean.rotate(-0.8, resample=Image.Resampling.BICUBIC, fillcolor=250)
        softened = tilted.filter(ImageFilter.GaussianBlur(0.7))
        grain = Image.frombytes("L", softened.size, random.Random(2024).randbytes(softened.width * softened.height))
        speckled = Image.blend(softened, grain, 0.07)
        photo = ImageOps.colorize(speckled, black="#20232a", white="#f3efe6")

        jpeg = io.BytesIO()
        photo.save(jpeg, format="JPEG", quality=62, optimize=False)
        jpeg.seek(0)

        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=A4, invariant=1)
        canvas.drawImage(ImageReader(jpeg), 0, 0, width=A4[0], height=A4[1])
        canvas.showPage()
        canvas.save()
        return buffer.getvalue()

    @staticmethod
    def lock(pdf_bytes: bytes) -> bytes:
        """Return the PDF protected by a password, as a supplier's locked e-invoice arrives."""
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes)))
        writer.encrypt(user_password="vertex-2024", algorithm="RC4-128")
        # pypdf draws the second file ID at random; only the first feeds the key, so repeating it keeps reruns equal.
        writer._ID = ArrayObject((writer._ID[0], writer._ID[0]))
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()


def main() -> None:
    """Write every invoice PDF and its .read.json into data/invoices."""
    backend = Path(__file__).resolve().parent.parent
    vendors = _Helper.load_vendors(backend / "data" / "vendors.csv") | _Helper.OFF_MASTER
    out_dir = backend / "data" / "invoices"
    out_dir.mkdir(parents=True, exist_ok=True)

    for spec in _Helper.invoice_specs():
        sheet = _Helper.build_sheet(spec, vendors[spec.vendor_id])
        pdf_bytes = _Helper.render_pdf(sheet)
        if spec.scanned:
            pdf_bytes = _Helper.scan(pdf_bytes)
        if spec.encrypted:
            pdf_bytes = _Helper.lock(pdf_bytes)
        (out_dir / f"{spec.file_id}.pdf").write_bytes(pdf_bytes)
        (out_dir / f"{spec.file_id}.read.json").write_text(json.dumps(sheet.read, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
