"""Findings: the coded, human-readable observations every stage produces."""

from typing import Literal

from pydantic import BaseModel


class _Types:
    """Finding codes, severities and the finding model."""

    type Severity = Literal["info", "warn", "block"]
    type FindingCode = Literal[
        "FILE_REJECTED",
        "EXTRACTION_FAILED",
        "MISSING_INVOICE_NUMBER",
        "MISSING_TOTAL",
        "MISSING_DATE",
        "DATE_AMBIGUOUS",
        "DATE_IN_FUTURE",
        "LINE_SUM_MISMATCH",
        "TAX_SUM_MISMATCH",
        "TAX_ID_FORMAT_INVALID",
        "TAXABLE_VALUE_DERIVED",
        "NON_POSITIVE_AMOUNT",
        "VENDOR_IDENTIFIED",
        "VENDOR_NOT_FOUND",
        "VENDOR_AMBIGUOUS",
        "VENDOR_NAME_ONLY",
        "VENDOR_SCORE_LOW",
        "VENDOR_SUSPENDED",
        "TAX_ID_MISMATCH",
        "BANK_ACCOUNT_MISMATCH",
        "BANK_ACCOUNT_VERIFIED",
        "BANK_DETAILS_ABSENT",
        "PO_MATCHED",
        "PO_NOT_FOUND",
        "PO_VENDOR_MISMATCH",
        "PO_CANCELLED",
        "PO_INFERRED",
        "PO_AMBIGUOUS",
        "PO_SELECTED_BY_JEV",
        "PO_UNUSED",
        "LINES_NOT_MATCHED",
        "LINES_MATCHED_BY_JEV",
        "CURRENCY_MISMATCH",
        "CURRENCY_NOT_READ",
        "AMOUNT_WITHIN_TOLERANCE",
        "PARTIAL_DELIVERY",
        "PO_FULLY_INVOICED",
        "OVER_CONSUMPTION",
        "QTY_EXCEEDS_ORDERED",
        "UNIT_PRICE_MISMATCH",
        "TAX_RATE_MISMATCH",
        "HEADER_LEVEL_ONLY",
        "NO_DUPLICATE",
        "DUPLICATE_EXACT",
        "DUPLICATE_SUSPECTED",
    ]

    class Finding(BaseModel):
        """One observation: a sentence an AP clerk can read, with the same numbers as strings in data."""

        code: _Types.FindingCode
        severity: _Types.Severity
        message: str
        data: dict[str, str]

        @classmethod
        def of(cls, code: _Types.FindingCode, severity: _Types.Severity, message: str, **data: str) -> _Types.Finding:
            """Build a finding with its numbers passed as keyword strings."""
            return cls(code=code, severity=severity, message=message, data=data)


class FindingTypes:
    """Public finding types."""

    type Severity = _Types.Severity
    type FindingCode = _Types.FindingCode
    Finding = _Types.Finding
