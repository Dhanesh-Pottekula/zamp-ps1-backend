"""
Stage 4, bank validation: is the account the invoice asks to be paid to the vendor master's.

A different account blocks at any vendor score (payment diversion), and so does the same domestic number at another
bank code; no printed account is noted and payment goes to the master account. Skipped when no vendor was matched.
"""

from typing import TYPE_CHECKING

from app.entities._types import FindingTypes, IdentityTypes, StageTypes

if TYPE_CHECKING:
    from app.entities._types import ExtractionTypes
    from app.entities.vendor_ent import VendorEntityClass


class BankStageClass:
    """Compare the payee account with the identified vendor's master account."""

    def __init__(self, vendor_entity: VendorEntityClass) -> None:
        """Keep the vendor master."""
        self._vendor_entity = vendor_entity

    async def run(self, vendor_id: str, bank: ExtractionTypes.BankDetails) -> StageTypes.BankCheck:
        """
        Compare the bank account the invoice asks to be paid to with the vendor master's account.

        A different account blocks at any vendor score (a classic payment-diversion fraud), and so does the same
        domestic number at a different bank code, exactly as identification compares payees; no printed account is
        noted and payment goes to the master account. Both accounts are shown masked to their last four digits.

        Raises:
            LookupError: the vendor is not in the master.

        """
        records = await self._vendor_entity.records()
        record = next((record for record in records if record.vendor_id == vendor_id), None)
        if record is None:
            message = f"vendor {vendor_id} is not in the master"
            raise LookupError(message)

        account = IdentityTypes.key(bank.account_number)
        master = IdentityTypes.masked(record.account_key)
        if not account:
            finding = FindingTypes.Finding.of(
                "BANK_DETAILS_ABSENT",
                "info",
                f"The invoice prints no bank account; payment goes to account {master} held in the vendor master for "
                f"{record.legal_name}.",
                master_account=master,
                vendor_id=vendor_id,
            )
            return StageTypes.BankCheck(
                vendor_id=vendor_id, state="absent", invoice_account=None, master_account=master, findings=[finding]
            )

        shown = IdentityTypes.masked(account)
        code = IdentityTypes.code_key(bank.bank_code)
        if IdentityTypes.same_payee(account, code, record.account_key, record.bank_code_key):
            finding = FindingTypes.Finding.of(
                "BANK_ACCOUNT_VERIFIED",
                "info",
                f"The invoice asks for payment to account {shown}, the account the vendor master holds for "
                f"{record.legal_name}.",
                invoice_account=shown,
                vendor_id=vendor_id,
            )
            return StageTypes.BankCheck(
                vendor_id=vendor_id, state="matched", invoice_account=shown, master_account=master, findings=[finding]
            )

        # The same number at another bank is another account: a domestic number repeats across banks.
        other_bank = IdentityTypes.same_account(account, record.account_key)
        message = (
            f"The invoice asks for payment to account {shown} at bank code {code}, but the vendor master holds that "
            f"number at bank code {record.bank_code_key} for {record.legal_name}."
            if other_bank
            else f"The invoice asks for payment to account {shown}, but the vendor master holds account {master} for "
            f"{record.legal_name}."
        )
        finding = FindingTypes.Finding.of(
            "BANK_ACCOUNT_MISMATCH",
            "block",
            f"{message} Verify the change with the vendor by phone before paying.",
            invoice_account=shown,
            master_account=master,
            vendor_id=vendor_id,
        )
        return StageTypes.BankCheck(
            vendor_id=vendor_id, state="mismatched", invoice_account=shown, master_account=master, findings=[finding]
        )
