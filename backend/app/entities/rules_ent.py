"""
AP policy shared by several stages: the tolerance rule, the GSTIN shape, the codes that reject outright, the sum
and rate bands, and the order reasons are listed in.

The checks themselves are stages: validation_stage, amounts_stage and decision_stage use this policy.
"""

import re
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from app.entities._types import AmountTypes, FindingTypes

if TYPE_CHECKING:
    from collections.abc import Mapping


class _Helper:
    """The GSTIN shape."""

    GSTIN: Final = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


class RulesEntityClass:
    """Hold the AP policy the validation, PO, amount and decision stages apply."""

    def __init__(
        self,
        tolerance_pct: Decimal,
        tolerance_floors: Mapping[str, Decimal],
        *,
        line_sure_above: float = 90.0,
        line_candidate_at: float = 70.0,
        line_coverage_at: float = 80.0,
    ) -> None:
        """Keep the tolerance policy, the line-match thresholds and the codes that reject outright."""
        self.line_sure_above: Final = line_sure_above
        self.line_candidate_at: Final = line_candidate_at
        self.line_coverage_at: Final = line_coverage_at
        self.tolerance_pct: Final = tolerance_pct
        self._tolerance_floors: Final = dict(tolerance_floors)
        self.reject_codes: Final[frozenset[FindingTypes.FindingCode]] = frozenset(
            {"FILE_REJECTED", "VENDOR_SUSPENDED", "PO_CANCELLED", "DUPLICATE_EXACT"}
        )
        self.rate_band: Final = Decimal("0.1")
        self.sum_band: Final = Decimal(1)

    def tolerance(self, remaining: Decimal, pct: Decimal, currency: str) -> Decimal:
        """Return max(pct percent of the remaining balance, the currency's floor); a currency with no floor has 0."""
        floor = self._tolerance_floors.get(currency, Decimal(0))
        return max(pct / AmountTypes.HUNDRED * remaining, floor).quantize(AmountTypes.CENT)

    @staticmethod
    def gstin_shaped(tax_key: str) -> bool:
        """Return whether an uppercase tax ID with only letters and digits has the shape of a GSTIN."""
        return _Helper.GSTIN.match(tax_key) is not None

    def ordered(self, findings: list[FindingTypes.Finding]) -> list[FindingTypes.Finding]:
        """Return the findings in reason order; the sort is stable, so each group keeps the given stage order."""
        return sorted(findings, key=self._rank)

    def _rank(self, finding: FindingTypes.Finding) -> int:
        """Order reasons: reject-set blocks, other blocks, warnings, information."""
        if finding.severity == "block":
            return 0 if finding.code in self.reject_codes else 1

        return 2 if finding.severity == "warn" else 3
