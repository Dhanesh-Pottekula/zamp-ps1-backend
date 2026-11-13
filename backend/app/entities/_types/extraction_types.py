"""
Extraction models: InvoiceRead is the only schema the model sees; InvoiceExtraction is its normalised form.

InvoiceRead has no optional fields, unions or open objects, so it stays inside structured-output limits; the
model writes "" for an absent value and copies amounts as printed, and code does all number parsing. Field
descriptions are allowed here because the schema is serialized to the model (TYP-3).
"""

from datetime import date  # noqa: TC003 - pydantic resolves field types at runtime
from decimal import Decimal  # noqa: TC003 - pydantic resolves field types at runtime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Types:
    """Parts of the wire and normalised models."""

    type DateOrder = Literal["DMY", "MDY", "YMD", ""]
    type DecimalSeparator = Literal[".", ",", ""]

    class WireModel(BaseModel):
        """Base for the wire schema: unknown keys are refused, so every object closes its properties."""

        model_config = ConfigDict(extra="forbid")

    class LineRead(WireModel):
        """One printed invoice line."""

        description: str = Field(description='Item or service description as printed, or "".')
        quantity: str = Field(description='Quantity exactly as printed, without the unit, or "".')
        unit: str = Field(description='Unit of measure as printed (pcs, kg, lot, hrs), or "".')
        unit_price: str = Field(description='Price per unit exactly as printed, or "".')
        line_amount: str = Field(description='Line amount exactly as printed, or "".')
        tax_rate_pct: str = Field(
            description='Total tax rate printed for this line in percent (for CGST 9% + SGST 9% write "18"; for '
            'MwSt. 19 % write "19"), or "".'
        )

    class TaxRead(WireModel):
        """One printed tax line."""

        label: str = Field(description='Tax name as printed, for example "CGST", "SGST", "IGST", "VAT", or "".')
        rate_pct: str = Field(description='Rate of this tax line in percent as printed, or "".')
        amount: str = Field(description='Amount of this tax line exactly as printed, or "".')

    class SourceRead(WireModel):
        """The printed text a key value was read from."""

        field: str = Field(
            description="Name of the field this quote supports: invoice_number, invoice_date, due_date, subtotal, "
            "total or po_reference_text."
        )
        text: str = Field(description="The printed text the value was read from, quoted verbatim with its label.")

    class Party(BaseModel):
        """A supplier or buyer identity."""

        name: str | None
        tax_id: str | None
        address: str | None
        email: str | None

    class BankDetails(BaseModel):
        """The supplier's bank details as printed."""

        account_name: str | None
        account_number: str | None
        bank_code: str | None
        bank_name: str | None

    class LineItem(BaseModel):
        """One normalised invoice line."""

        description: str | None
        quantity: Decimal | None
        unit: str | None
        unit_price: Decimal | None
        line_amount: Decimal | None
        tax_rate_pct: Decimal | None

    class TaxLine(BaseModel):
        """One normalised tax line."""

        label: str | None
        rate_pct: Decimal | None
        amount: Decimal | None

    class SourceQuote(BaseModel):
        """A quoted source text for one field."""

        field: str
        text: str

    class InvoiceRead(WireModel):
        """Everything printed on one invoice, as text, for code to normalise."""

        supplier_name: str = Field(description='Legal name of the seller who issued the invoice, or "".')
        supplier_tax_id: str = Field(
            description='Tax registration number of the SELLER (GSTIN, VAT number, EIN), never the buyer\'s, or "".'
        )
        supplier_address: str = Field(description='Seller address on one line, or "".')
        supplier_email: str = Field(description='Seller email address, or "".')
        bank_account_name: str = Field(description='Account holder name in the seller\'s bank details, or "".')
        bank_account_number: str = Field(description='Seller bank account number or IBAN exactly as printed, or "".')
        bank_code: str = Field(
            description='Seller bank or branch code as printed (IFSC, SWIFT/BIC, ABA routing number, sort code), or "".'
        )
        bank_name: str = Field(description='Name of the seller\'s bank, or "".')
        buyer_name: str = Field(description='Legal name of the customer being billed, or "".')
        buyer_tax_id: str = Field(description='Tax registration number of the customer being billed, or "".')
        invoice_number: str = Field(description='The invoice number exactly as printed, or "".')
        invoice_date: str = Field(description='Invoice date as YYYY-MM-DD, or "".')
        due_date: str = Field(description='Payment due date as YYYY-MM-DD, or "".')
        currency: str = Field(
            description="ISO 4217 currency code of the amounts (INR for ₹ or Rs., USD for US$, EUR for €, GBP for £), "
            'or "".'
        )
        date_order: _Types.DateOrder = Field(
            description='How this document prints numeric dates: "DMY" (04/11/2024 is 4 November), "MDY" (04/11/2024 '
            'is April 11) or "YMD". Judge it from the issuer\'s country (address, currency, tax ID) and from any date '
            'on the page whose day is above 12 or whose month is a word. "" when nothing on the document tells.'
        )
        decimal_separator: _Types.DecimalSeparator = Field(
            description='How this document writes amounts: "." when the decimal point is a dot and commas group '
            'thousands (1,234.56; 1,234), "," when the decimal point is a comma and dots or spaces group thousands '
            '(1.234,56; 1.234; 1 234). "" only when no amount has decimals or thousands at all.'
        )
        po_reference_text: str = Field(
            description='Any purchase order or order reference printed on the invoice, verbatim, or "".'
        )
        prices_include_tax: bool = Field(
            description="True when the printed unit prices and line amounts already include tax (gross prices, for "
            "example 'prices include VAT'), even if the tax amount is also shown separately; false when tax is added "
            "on top of the lines."
        )
        lines: list[_Types.LineRead] = Field(description="Every printed line item, in order.")
        subtotal: str = Field(description='Pre-tax subtotal / taxable value exactly as printed, or "".')
        tax_lines: list[_Types.TaxRead] = Field(description="Every printed tax line, in order.")
        total: str = Field(description='Grand total payable exactly as printed, or "".')
        sources: list[_Types.SourceRead] = Field(
            description="Verbatim source quotes for invoice_number, invoice_date, due_date, subtotal, total and "
            "po_reference_text, one per field found."
        )

    class InvoiceExtraction(BaseModel):
        """
        The normalised invoice every later stage reads.

        ambiguous_dates names the date fields (invoice_date, due_date) whose printed form reads either way, both
        numbers being 12 or less, on a document that does not show its date order; they were read day first.
        """

        supplier: _Types.Party
        supplier_bank: _Types.BankDetails
        buyer: _Types.Party
        invoice_number: str | None
        invoice_date: date | None
        due_date: date | None
        ambiguous_dates: list[str]
        currency: str | None
        po_reference_text: str | None
        prices_include_tax: bool
        lines: list[_Types.LineItem]
        subtotal: Decimal | None
        tax_lines: list[_Types.TaxLine]
        total: Decimal | None
        sources: list[_Types.SourceQuote]


class ExtractionTypes:
    """Public extraction types."""

    Party = _Types.Party
    BankDetails = _Types.BankDetails
    LineItem = _Types.LineItem
    TaxLine = _Types.TaxLine
    SourceQuote = _Types.SourceQuote
    InvoiceRead = _Types.InvoiceRead
    InvoiceExtraction = _Types.InvoiceExtraction
