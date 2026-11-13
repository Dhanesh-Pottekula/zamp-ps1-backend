"""The vendor identification ladder."""

import pytest

from app.entities import EntitiesRegistry, ExtractionTypes
from app.entities._types import IdentityTypes as VendorHelper


def party(name: str = "", tax_id: str = "") -> ExtractionTypes.Party:
    return ExtractionTypes.Party(name=name or None, tax_id=tax_id or None, address=None, email=None)


def bank(account: str = "") -> ExtractionTypes.BankDetails:
    return ExtractionTypes.BankDetails(
        account_name=None, account_number=account or None, bank_code=None, bank_name=None
    )


async def test_tax_id_wins(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("Someone Else", "29 AACCA 1234 F1Z5"), bank("50200012345678"))

    assert (match.vendor_id, match.method, match.score) == ("V001", "tax_id", 100.0)
    assert [finding.code for finding in match.findings] == ["VENDOR_IDENTIFIED"]


async def test_bank_account_and_name_identify_when_tax_id_is_missing(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("Zenith Traders"), bank("918020045678901"))

    assert (match.vendor_id, match.method, match.score) == ("V002", "bank_account", 90.0)
    assert match.findings[0].message == "Vendor Zenith Traders LLP identified by bank account ***********8901."
    assert [(signal.signal, signal.state, signal.points) for signal in match.evidence] == [
        ("tax_id", "absent", 0.0),
        ("bank_account", "matched", 60.0),
        ("name", "matched", 30.0),
    ]


async def test_one_identifier_alone_is_held_for_a_person(registry: type[EntitiesRegistry]) -> None:
    by_account = await registry.VendorStage.run(party("Unreadable"), bank("918020045678901"))
    by_tax = await registry.VendorStage.run(party("Unreadable", "27AAEFZ5678K1Z3"), bank())

    assert (by_account.vendor_id, by_account.method, by_account.score, by_account.needed) == (
        "V002",
        "bank_account",
        60.0,
        80.0,
    )
    assert (by_tax.vendor_id, by_tax.method, by_tax.score) == ("V002", "tax_id", 80.0)
    for match in (by_account, by_tax):
        assert [(finding.code, finding.severity) for finding in match.findings] == [("VENDOR_SCORE_LOW", "block")]


async def test_a_name_alone_scores_below_the_pass_mark(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("ACME SUPPLIES PRIVATE LIMITED"), bank())

    assert (match.score, match.needed) == (30.0, 80.0)


async def test_another_vendors_tax_id_blocks_a_bank_and_name_match(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("Zenith Traders", "29AAKFP2468M1Z6"), bank("918020045678901"))

    assert (match.vendor_id, match.score) == ("V002", 90.0)
    assert [(finding.code, finding.severity) for finding in match.findings] == [
        ("VENDOR_IDENTIFIED", "info"),
        ("TAX_ID_MISMATCH", "block"),
    ]


async def test_identifiers_of_two_vendors_never_pay_unchecked(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("Acme Supplies", "29AACCA1234F1Z5"), bank("918020045678901"))
    backed = await registry.VendorStage.run(party("Zenith Traders", "29AACCA1234F1Z5"), bank("918020045678901"))

    paid = await registry.BankStage.run("V001", bank("918020045678901"))

    assert (match.vendor_id, [finding.code for finding in match.findings]) == ("V001", ["VENDOR_IDENTIFIED"])
    assert (paid.state, [finding.code for finding in paid.findings]) == ("mismatched", ["BANK_ACCOUNT_MISMATCH"])
    assert backed.vendor_id == "V002"
    assert [finding.code for finding in backed.findings] == ["VENDOR_IDENTIFIED", "TAX_ID_MISMATCH"]


async def test_two_vendors_above_the_pass_mark_are_ambiguous(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("Nova Component Pvt Ltd", "33AAHCN9753Q1Z4"), bank("60234567890"))

    assert (match.vendor_id, match.score) == (None, 0.0)
    assert [(candidate.vendor_id, candidate.score) for candidate in match.candidates] == [
        ("V006", 100.0),
        ("V003", 90.0),
    ]
    assert [(finding.code, finding.severity) for finding in match.findings] == [("VENDOR_AMBIGUOUS", "block")]


async def test_fuzzy_name_alone_blocks(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("ACME SUPPLIES PRIVATE LIMITED"), bank())

    assert (match.vendor_id, match.method) == ("V001", "name_fuzzy")
    assert [(finding.code, finding.severity) for finding in match.findings] == [("VENDOR_NAME_ONLY", "block")]


async def test_close_names_are_ambiguous(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("Nova Component Pvt Ltd"), bank())

    assert match.vendor_id is None
    assert {candidate.vendor_id for candidate in match.candidates} >= {"V003", "V006"}
    assert [finding.code for finding in match.findings] == ["VENDOR_AMBIGUOUS"]


async def test_unknown_supplier_is_not_found(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("Quantum Widgets Corp"), bank("123"))

    assert match.vendor_id is None
    assert [finding.code for finding in match.findings] == ["VENDOR_NOT_FOUND"]


async def test_suspended_look_alike_is_blocked(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("Nova Component Systems Pvt Ltd", "33AAHCN9753Q1Z4"), bank())

    assert match.vendor_id == "V006"
    assert [finding.code for finding in match.findings] == ["VENDOR_IDENTIFIED", "VENDOR_SUSPENDED"]


async def test_changed_bank_account_is_a_fraud_signal(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("Pixel Print Solutions", "29AAKFP2468M1Z6"), bank("77700011122"))
    check = await registry.BankStage.run("V005", bank("77700011122"))

    mismatch = check.findings[-1]
    assert match.vendor_id == "V005"
    assert "BANK_ACCOUNT_MISMATCH" not in [finding.code for finding in match.findings]
    assert (check.state, check.invoice_account, check.master_account) == ("mismatched", "*******1122", "*******4321")
    assert (mismatch.code, mismatch.severity) == ("BANK_ACCOUNT_MISMATCH", "block")
    assert mismatch.data == {"invoice_account": "*******1122", "master_account": "*******4321", "vendor_id": "V005"}
    assert "1122" in mismatch.message
    assert "4321" in mismatch.message
    assert "77700011122" not in mismatch.message


async def test_vat_number_without_its_country_prefix_identifies(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("Harbour & Finch", "123 4567 89"), bank())

    assert (match.vendor_id, match.method) == ("V009", "tax_id")
    assert match.findings[0].message == "Vendor Harbour & Finch Ltd identified by tax ID 123456789."


async def test_a_different_vat_prefix_never_matches(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("", "AT287654321"), bank())

    assert match.vendor_id is None


async def test_an_iban_pays_the_domestic_account_it_contains(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("", "DE287654321"), bank("0532013000"))
    by_iban = await registry.VendorStage.run(party("Unreadable"), bank("DE89 3704 0044 0532 0130 00"))

    assert (match.vendor_id, [finding.code for finding in match.findings]) == ("V008", ["VENDOR_IDENTIFIED"])
    assert (by_iban.vendor_id, by_iban.method) == ("V008", "bank_account")


async def test_accented_and_dotted_names_fold(registry: type[EntitiesRegistry]) -> None:
    match = await registry.VendorStage.run(party("KESSLER INDUSTRIETECHNIK G.m.b.H."), bank())

    assert (match.vendor_id, match.method) == ("V008", "name_fuzzy")


def test_name_folding_keeps_every_script() -> None:
    assert VendorHelper.core_name("Müller Präzision GmbH") == "muller prazision"
    assert VendorHelper.core_name("Rossi S.p.A.") == "rossi"
    assert VendorHelper.core_name("ООО Ромашка") == "ооо ромашка"  # noqa: RUF001 - Cyrillic on purpose
    assert VendorHelper.core_name("株式会社トヨタ") != ""


async def test_bank_stage_verifies_or_notes_a_missing_account(registry: type[EntitiesRegistry]) -> None:
    same = await registry.BankStage.run("V005", bank("1098 7654 321"))
    absent = await registry.BankStage.run("V005", bank())

    assert (same.state, [(f.code, f.severity) for f in same.findings]) == (
        "matched",
        [("BANK_ACCOUNT_VERIFIED", "info")],
    )
    assert (absent.state, absent.invoice_account, [(f.code, f.severity) for f in absent.findings]) == (
        "absent",
        None,
        [("BANK_DETAILS_ABSENT", "info")],
    )
    assert "4321" in absent.findings[0].message


async def test_bank_stage_needs_a_master_vendor(registry: type[EntitiesRegistry]) -> None:
    with pytest.raises(LookupError):
        await registry.BankStage.run("V999", bank("1"))


async def test_bank_stage_blocks_the_same_number_at_another_bank(registry: type[EntitiesRegistry]) -> None:
    moved = ExtractionTypes.BankDetails(
        account_name=None, account_number="43218765", bank_code="30-96-34", bank_name=None
    )
    kept = moved.model_copy(update={"bank_code": "20-45-77"})

    diverted = await registry.BankStage.run("V009", moved)
    same = await registry.BankStage.run("V009", kept)

    assert (diverted.state, [f.code for f in diverted.findings]) == ("mismatched", ["BANK_ACCOUNT_MISMATCH"])
    assert "bank code" in diverted.findings[0].message
    assert same.state == "matched"
