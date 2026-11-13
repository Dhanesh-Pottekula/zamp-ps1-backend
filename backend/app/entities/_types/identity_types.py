"""
Normalised identifiers for comparing what an invoice prints with the master data and the invoice register.

Vendor creation, vendor matching, bank validation, invoice registration and the duplicate check all compare through
these one definitions, so two stages can never disagree about whether two accounts or numbers are the same.
"""

import json
import re
import unicodedata
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from app.entities._types.extraction_types import ExtractionTypes


class IdentityTypes:
    """Normalise PO numbers, tax IDs, bank accounts, bank codes, names and invoice numbers, and compare them."""

    # A PO number: printed as PO-2024-0142, PO 2024 0142, PO/2024/0142 or bare 2024-0142.
    PO_PATTERN: Final = re.compile(r"(?<![A-Za-z0-9])PO[-\s/]?(\d{4})[-\s/]?(\d{4})(?!\d)", re.IGNORECASE)
    BARE_PATTERN: Final = re.compile(r"(?<![\dA-Za-z-])(\d{4})-(\d{4})(?![\d-])")
    PO_LIKE: Final = re.compile(
        r"(?<![A-Za-z0-9])P\.?\s?O\.?(?:\s*(?:No|Number|Ref)\.?)?[\s:#/-]*\d|\d{4}-\d{4}", re.IGNORECASE
    )
    VAT_PREFIX: Final = re.compile(r"[A-Z]{2}(?=\d)")
    IBAN: Final = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}")
    SUFFIXES: Final = frozenset(
        {
            "pvt",
            "ltd",
            "limited",
            "private",
            "inc",
            "llc",
            "llp",
            "co",
            "corp",
            "technologies",
            "india",
            "plc",
            "gmbh",
            "ag",
            "kg",
            "sa",
            "sas",
            "sarl",
            "bv",
            "nv",
            "srl",
            "spa",
            "ltda",
            "pty",
            "oy",
            "ab",
            "kk",
        }
    )

    @staticmethod
    def key(value: str | None) -> str:
        """Uppercase and strip everything but letters and digits."""
        return re.sub(r"[^0-9A-Z]", "", (value or "").upper())

    @staticmethod
    def code_key(value: str | None) -> str:
        """Normalise a bank code; an 11-character BIC ending in XXX (the head office) equals its 8-character form."""
        code = IdentityTypes.key(value)
        return code[:8] if len(code) == 11 and code.endswith("XXX") else code

    @staticmethod
    def same_tax_id(printed: str, stored: str) -> bool:
        """
        Compare two tax-ID keys, allowing one side to drop its two-letter country prefix.

        A UK invoice may print "VAT Reg No 123 4567 89" for GB123456789, a German one DE287654321 or 287654321. Two
        different prefixes never match, and a GSTIN starts with digits, so it is compared exactly.
        """
        if not printed or not stored:
            return False

        def bare(key: str) -> str:
            return key[2:] if IdentityTypes.VAT_PREFIX.match(key) else key

        return printed == stored or bare(printed) == stored or printed == bare(stored)

    @staticmethod
    def same_account(printed: str, stored: str) -> bool:
        """
        Compare two account keys; an IBAN equals the domestic account number it ends with.

        GB29NWBK60161331926819 holds account 31926819 and DE89370400440532013000 holds 0532013000, so an invoice
        printing either form pays the same account. The shorter number must have at least six characters.
        """
        if not printed or not stored:
            return False

        if printed == stored:
            return True

        long, short = (printed, stored) if len(printed) > len(stored) else (stored, printed)
        return bool(IdentityTypes.IBAN.fullmatch(long)) and len(short) >= 6 and long.endswith(short)

    @staticmethod
    def core_name(value: str) -> str:
        """
        Fold a company name for comparison: drop accents and dots, keep letters of any script, drop legal suffixes.

        "Müller GmbH" becomes "muller", "Rossi S.p.A." becomes "rossi", and Cyrillic or Japanese names keep their
        letters instead of reducing to nothing.
        """
        folded = "".join(char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char))
        words = re.sub(r"[\W_]+", " ", folded.lower().replace(".", "")).split()
        kept = [word for word in words if word not in IdentityTypes.SUFFIXES]
        return " ".join(kept or words)

    @staticmethod
    def state(printed: str, stored: str, same: bool) -> Literal["matched", "mismatched", "absent"]:
        """Return absent when either side has no value, else whether the two agree."""
        if not printed or not stored:
            return "absent"

        return "matched" if same else "mismatched"

    @staticmethod
    def masked(account: str) -> str:
        """Show only the last four characters of an account number."""
        return "*" * max(len(account) - 4, 0) + account[-4:]

    @staticmethod
    def same_payee(account_key: str, bank_code_key: str, stored_account: str, stored_code: str) -> bool:
        """
        Return whether an account (and bank code) is the stored one.

        An IBAN is unique on its own. A domestic number such as a UK 8-digit account repeats across banks, so when
        both sides carry a bank code they must agree too.
        """
        if not IdentityTypes.same_account(account_key, stored_account):
            return False

        international = IdentityTypes.IBAN.fullmatch(account_key) or IdentityTypes.IBAN.fullmatch(stored_account)
        return bool(international) or not bank_code_key or not stored_code or bank_code_key == stored_code

    @staticmethod
    def number_key(number: str | None) -> str | None:
        """Uppercase and strip spaces, dashes and slashes; any other difference is a different number."""
        return re.sub(r"[\s\-/]", "", number.upper()) if number else None

    @staticmethod
    def fingerprint(lines: list[ExtractionTypes.LineItem]) -> str:
        """Return the sorted (description, quantity, unit price) tuples of the lines as JSON."""
        rows = sorted(
            (
                " ".join(re.sub(r"[^0-9a-z]+", " ", (line.description or "").lower()).split()),
                f"{line.quantity.normalize():f}" if line.quantity is not None else "",
                f"{line.unit_price.normalize():f}" if line.unit_price is not None else "",
            )
            for line in lines
        )
        return json.dumps(rows)
