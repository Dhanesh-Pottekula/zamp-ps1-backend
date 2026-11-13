"""
Stage 3, vendor matching: who issued the invoice.

Each printed identity field (tax ID, bank account, legal name) that equals the master earns its weight; a vendor is
identified only when the weights add up to more than the pass score. A name alone is matched fuzzily and never
enough to pay. Suspension and a different printed tax ID block at any score. The payee account is checked in the
next stage (bank validation).
"""

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel

from app.entities._types import FindingTypes, IdentityTypes, StageTypes

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.adapters import AdapterTypes
    from app.entities._types import ExtractionTypes
    from app.entities.rules_ent import RulesEntityClass
    from app.entities.vendor_ent import VendorEntityClass, VendorTypes


class _Types:
    """The printed identity fields, normalised."""

    class Printed(BaseModel):
        """The identity fields printed on an invoice, normalised for comparison."""

        tax_key: str
        account_key: str
        code_key: str
        core_name: str


class _Helper:
    """Wording for the identity fields."""

    SIGNAL_WORDS: Final[dict[str, str]] = {
        "tax_id": "the tax ID",
        "bank_account": "the bank account",
        "name": "the name",
    }


class VendorStageClass:
    """Identify the vendor by scoring the printed identity fields against every master vendor."""

    def __init__(
        self,
        vendor_entity: VendorEntityClass,
        match_adapter: AdapterTypes.Match,
        *,
        rules_entity: RulesEntityClass,
        weights: Mapping[StageTypes.SignalName, float],
        pass_score: float,
    ) -> None:
        """Keep the vendor master, the fuzzy matcher, the GSTIN rule, the weights and the thresholds."""
        self._vendor_entity = vendor_entity
        self._match_adapter = match_adapter
        self._rules_entity = rules_entity

        self._weights: Final = dict(weights)
        self._pass_score: Final = pass_score

        self._accept_score: Final = 90.0
        self._rival_score: Final = 85.0
        self._candidate_score: Final = 70.0

    async def run(self, supplier: ExtractionTypes.Party, bank: ExtractionTypes.BankDetails) -> StageTypes.VendorMatch:
        """
        Identify the vendor by scoring the printed tax ID, bank account and name against every master vendor.

        A vendor is identified only when its matched fields add up to more than the pass score and no other vendor
        fits as well; a lower score proposes the vendor and blocks. A name alone is handled by the name match.
        """
        records = await self._vendor_entity.records()
        printed = _Types.Printed(
            tax_key=IdentityTypes.key(supplier.tax_id),
            account_key=IdentityTypes.key(bank.account_number),
            code_key=IdentityTypes.code_key(bank.bank_code),
            core_name=IdentityTypes.core_name(supplier.name or ""),
        )
        ranked = sorted(
            ((self._evidence(record, printed), record) for record in records),
            key=lambda pair: self._total(pair[0]),
            reverse=True,
        )
        strong = [pair for pair in ranked if self._strong(pair[0])]
        if not strong:
            evidence = {record.vendor_id: signals for signals, record in ranked}
            match = self._by_name(supplier.name, records, evidence)
        elif self._contested(strong):
            return self._conflict(strong)
        else:
            match = self._scored(strong[0][1], strong[0][0], printed)

        record = next((record for record in records if record.vendor_id == match.vendor_id), None)
        if record is not None:
            match.findings.extend(self._vetoes(record, match.evidence, printed))

        return match

    def _evidence(self, record: VendorTypes.Record, printed: _Types.Printed) -> list[StageTypes.VendorSignal]:
        """Compare each printed identity field with one vendor; a field that matches earns its weight."""
        similar = self._match_adapter.ratio(printed.core_name, IdentityTypes.core_name(record.legal_name))
        states: dict[StageTypes.SignalName, StageTypes.SignalState] = {
            "tax_id": IdentityTypes.state(
                printed.tax_key, record.tax_key, IdentityTypes.same_tax_id(printed.tax_key, record.tax_key)
            ),
            "bank_account": IdentityTypes.state(
                printed.account_key,
                record.account_key,
                IdentityTypes.same_payee(
                    printed.account_key, printed.code_key, record.account_key, record.bank_code_key
                ),
            ),
            "name": IdentityTypes.state(printed.core_name, record.legal_name, similar >= self._accept_score),
        }
        return [
            StageTypes.VendorSignal(
                signal=signal,
                state=state,
                weight=self._weights[signal],
                points=self._weights[signal] if state == "matched" else 0.0,
            )
            for signal, state in states.items()
        ]

    @staticmethod
    def _total(evidence: list[StageTypes.VendorSignal]) -> float:
        """Add up the points of the matched fields, capped at 100."""
        return min(100.0, sum(signal.points for signal in evidence))

    @staticmethod
    def _strong(evidence: list[StageTypes.VendorSignal]) -> bool:
        """Return whether a unique identifier (tax ID or bank account) matched, not the name alone."""
        return any(signal.state == "matched" and signal.signal != "name" for signal in evidence)

    def _contested(self, strong: list[tuple[list[StageTypes.VendorSignal], VendorTypes.Record]]) -> bool:
        """Return whether a second vendor fits as well as the first, or also clears the pass score."""
        if len(strong) < 2:
            return False

        rival = self._total(strong[1][0])
        return rival > self._pass_score or rival == self._total(strong[0][0])

    def _scored(
        self, record: VendorTypes.Record, evidence: list[StageTypes.VendorSignal], printed: _Types.Printed
    ) -> StageTypes.VendorMatch:
        """Build the match for the vendor a unique identifier points to: identified above the pass score, else held."""
        matched = [signal.signal for signal in evidence if signal.state == "matched"]
        method: StageTypes.VendorMethod = "tax_id" if "tax_id" in matched else "bank_account"
        total = self._total(evidence)
        if total > self._pass_score:
            label = "GSTIN" if self._rules_entity.gstin_shaped(printed.tax_key) else "tax ID"
            identifier = (
                f"{label} {printed.tax_key}"
                if method == "tax_id"
                else f"bank account {IdentityTypes.masked(printed.account_key)}"
            )
            finding = FindingTypes.Finding.of(
                "VENDOR_IDENTIFIED",
                "info",
                f"Vendor {record.legal_name} identified by {identifier}.",
                vendor_id=record.vendor_id,
                method=method,
            )
        else:
            fields = " and ".join(_Helper.SIGNAL_WORDS[signal] for signal in matched)
            finding = FindingTypes.Finding.of(
                "VENDOR_SCORE_LOW",
                "block",
                f"The invoice matches {record.legal_name} on {fields} only, which scores {total:.0f}; more than "
                f"{self._pass_score:.0f} is needed to identify a vendor, so a person must confirm it.",
                vendor_id=record.vendor_id,
                score=f"{total:.0f}",
                needed=f"{self._pass_score:.0f}",
            )

        return self._matched(record, method, evidence, [], [finding])

    def _conflict(
        self, strong: list[tuple[list[StageTypes.VendorSignal], VendorTypes.Record]]
    ) -> StageTypes.VendorMatch:
        """Refuse to choose when the printed identifiers point to more than one vendor."""
        candidates = [
            StageTypes.VendorCandidate(
                vendor_id=record.vendor_id, legal_name=record.legal_name, score=self._total(evidence), basis="weights"
            )
            for evidence, record in strong
        ]
        names = ", ".join(f"{candidate.legal_name} (score {candidate.score:.0f})" for candidate in candidates)
        finding = FindingTypes.Finding.of(
            "VENDOR_AMBIGUOUS",
            "block",
            f"The tax ID, bank account and name on the invoice point to more than one vendor: {names}. Choose the "
            "right vendor.",
            candidates=",".join(candidate.vendor_id for candidate in candidates),
        )
        return self._unmatched(candidates, finding)

    def _vetoes(
        self, record: VendorTypes.Record, evidence: list[StageTypes.VendorSignal], printed: _Types.Printed
    ) -> list[FindingTypes.Finding]:
        """Block a suspended vendor, and a printed tax ID that differs from the master, at any score."""
        findings: list[FindingTypes.Finding] = []
        if record.status == "suspended":
            findings.append(
                FindingTypes.Finding.of(
                    "VENDOR_SUSPENDED",
                    "block",
                    f"{record.legal_name} ({record.vendor_id}) is suspended in the vendor master; it must not be paid.",
                    vendor_id=record.vendor_id,
                )
            )

        if any(signal.signal == "tax_id" and signal.state == "mismatched" for signal in evidence):
            findings.append(
                FindingTypes.Finding.of(
                    "TAX_ID_MISMATCH",
                    "block",
                    f"The invoice shows tax ID {printed.tax_key}, but the vendor master holds {record.tax_key} for "
                    f"{record.legal_name}. Confirm which company is invoicing before paying.",
                    invoice_tax_id=printed.tax_key,
                    master_tax_id=record.tax_key,
                    vendor_id=record.vendor_id,
                )
            )

        return findings

    def _by_name(
        self, name: str | None, records: list[VendorTypes.Record], evidence: Mapping[str, list[StageTypes.VendorSignal]]
    ) -> StageTypes.VendorMatch:
        """Match on the fuzzy legal name: one clear winner (held for a person), ambiguous candidates, or nothing."""
        core = IdentityTypes.core_name(name or "")
        scored = sorted(
            (
                (self._match_adapter.ratio(core, IdentityTypes.core_name(record.legal_name)), record)
                for record in records
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        candidates = [
            StageTypes.VendorCandidate(
                vendor_id=record.vendor_id, legal_name=record.legal_name, score=round(score, 1), basis="name"
            )
            for score, record in scored
            if core and score >= self._candidate_score
        ]
        best = scored[0] if core and scored else (0.0, None)
        rival = scored[1][0] if len(scored) > 1 else 0.0
        if best[1] is not None and best[0] >= self._accept_score and rival < self._rival_score:
            record = best[1]
            warning = FindingTypes.Finding.of(
                "VENDOR_NAME_ONLY",
                "block",
                f"The vendor was identified as {record.legal_name} by name only ({best[0]:.0f}% similar); neither the "
                "tax ID nor the bank account matched the master, so a person must confirm it.",
                vendor_id=record.vendor_id,
                score=f"{best[0]:.1f}",
            )
            return self._matched(record, "name_fuzzy", evidence[record.vendor_id], candidates, [warning])

        if candidates:
            names = ", ".join(f"{candidate.legal_name} ({candidate.score:.0f}%)" for candidate in candidates)
            finding = FindingTypes.Finding.of(
                "VENDOR_AMBIGUOUS",
                "block",
                f"The supplier name '{name}' is close to more than one vendor: {names}. Choose the right vendor.",
                candidates=",".join(candidate.vendor_id for candidate in candidates),
            )
            return self._unmatched(candidates, finding)

        finding = FindingTypes.Finding.of(
            "VENDOR_NOT_FOUND",
            "block",
            f"No vendor in the master matches the supplier '{name or 'unknown'}' by tax ID, bank account or name.",
            supplier=name or "",
        )
        return self._unmatched([], finding)

    def _matched(
        self,
        record: VendorTypes.Record,
        method: StageTypes.VendorMethod,
        evidence: list[StageTypes.VendorSignal],
        candidates: list[StageTypes.VendorCandidate],
        findings: list[FindingTypes.Finding],
    ) -> StageTypes.VendorMatch:
        """Build the match for a chosen vendor, scored on its evidence."""
        return StageTypes.VendorMatch(
            vendor_id=record.vendor_id,
            legal_name=record.legal_name,
            method=method,
            score=self._total(evidence),
            needed=self._pass_score,
            evidence=evidence,
            candidates=candidates,
            findings=findings,
        )

    def _unmatched(
        self, candidates: list[StageTypes.VendorCandidate], finding: FindingTypes.Finding
    ) -> StageTypes.VendorMatch:
        """Build the result for an invoice no single vendor could be chosen for."""
        return StageTypes.VendorMatch(
            vendor_id=None,
            legal_name=None,
            method="none",
            score=0.0,
            needed=self._pass_score,
            evidence=[],
            candidates=candidates,
            findings=[finding],
        )
