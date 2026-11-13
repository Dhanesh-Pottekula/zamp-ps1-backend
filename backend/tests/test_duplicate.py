"""Duplicate detection against the invoice register."""

from decimal import Decimal
from typing import TYPE_CHECKING

from conftest import extraction

if TYPE_CHECKING:
    from app.adapters import AdapterRegistry
    from app.entities import EntitiesRegistry, StageTypes

TAXABLE = Decimal("36000.00")


async def register(
    registry: type[EntitiesRegistry], adapter: AdapterRegistry, outcome: StageTypes.Outcome = "approved"
) -> None:
    original = extraction("INV-001")
    await adapter.DB.run(
        lambda session: registry.Invoice.register_in(
            session, "run-1", original, vendor_id="V001", po_number="PO-2024-0142", taxable=TAXABLE, outcome=outcome
        )
    )


async def test_same_number_is_an_exact_duplicate(registry: type[EntitiesRegistry], adapter: AdapterRegistry) -> None:
    await register(registry, adapter)

    result = await registry.DuplicateStage.run("V001", extraction("INV-021", invoice_number="acm 24-25 0187"), TAXABLE)

    assert (result.verdict, result.prior_run) == ("exact", "run-1")
    assert [(finding.code, finding.severity) for finding in result.findings] == [("DUPLICATE_EXACT", "block")]


async def test_changed_suffix_is_only_suspected(registry: type[EntitiesRegistry], adapter: AdapterRegistry) -> None:
    await register(registry, adapter)

    result = await registry.DuplicateStage.run("V001", extraction("INV-021", invoice_number="ACM/24-25/0187A"), TAXABLE)

    assert (result.verdict, result.prior_run) == ("suspected", "run-1")
    assert result.findings[0].code == "DUPLICATE_SUSPECTED"


async def test_same_lines_on_another_date_are_suspected(
    registry: type[EntitiesRegistry], adapter: AdapterRegistry
) -> None:
    await register(registry, adapter)

    resend = extraction("INV-001", invoice_number="ACM/24-25/0999", invoice_date="2024-11-30")
    result = await registry.DuplicateStage.run("V001", resend, TAXABLE)

    assert result.verdict == "suspected"
    assert result.findings[0].data["basis"] == "line items"


async def test_different_amount_or_vendor_is_not_a_duplicate(
    registry: type[EntitiesRegistry], adapter: AdapterRegistry
) -> None:
    await register(registry, adapter)

    other_amount = await registry.DuplicateStage.run("V001", extraction("INV-024"), Decimal("24000.00"))
    other_vendor = await registry.DuplicateStage.run("V002", extraction("INV-001"), TAXABLE)

    assert other_amount.verdict == "none"
    assert other_vendor.verdict == "none"


async def test_rejected_invoices_do_not_count(registry: type[EntitiesRegistry], adapter: AdapterRegistry) -> None:
    await register(registry, adapter, "rejected")

    result = await registry.DuplicateStage.run("V001", extraction("INV-001"), TAXABLE)

    assert result.verdict == "none"
