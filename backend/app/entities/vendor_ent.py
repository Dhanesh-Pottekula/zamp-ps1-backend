"""
Vendor master: the vendors table, vendor creation and the reads the vendor and bank stages compare against.

Identification (stages/vendor_stage.py) and bank validation (stages/bank_stage.py) rely on tax IDs and bank accounts
being unique, so creation refuses a second vendor with either, compared through the same IdentityTypes rules.
"""

import asyncio
import re
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, field_validator
from sqlalchemy import String, delete, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters import Base, DecimalText
from app.entities._types import IdentityTypes, MasterTypes

if TYPE_CHECKING:
    from collections.abc import Collection

    from sqlalchemy.orm import Session

    from app.adapters import AdapterTypes
    from app.entities.rules_ent import RulesEntityClass


class VendorDB:
    """Vendor tables."""

    class Vendors(Base):
        """One approved or suspended supplier from the vendor master."""

        __tablename__ = "vendors"

        vendor_id: Mapped[str] = mapped_column(String(16), primary_key=True)
        legal_name: Mapped[str] = mapped_column(String(200))
        country: Mapped[str] = mapped_column(String(2))
        tax_id: Mapped[str] = mapped_column(String(32))
        pan: Mapped[str] = mapped_column(String(16))
        bank_account: Mapped[str] = mapped_column(String(34))
        bank_code: Mapped[str] = mapped_column(String(16))
        bank_name: Mapped[str] = mapped_column(String(100))
        currency: Mapped[str] = mapped_column(String(3))
        email_domain: Mapped[str] = mapped_column(String(100))
        payment_terms: Mapped[str] = mapped_column(String(32))
        status: Mapped[str] = mapped_column(String(16))
        tolerance_pct: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)


class _Types:
    """The vendor row, a new vendor as entered, and the in-memory vendor record."""

    type Status = Literal["approved", "suspended"]

    class Vendor(BaseModel):
        """One vendor master row, as seeded from vendors.csv or read from the table."""

        vendor_id: str
        legal_name: str
        country: str
        tax_id: str
        pan: str
        bank_account: str
        bank_code: str
        bank_name: str
        currency: str
        email_domain: str
        payment_terms: str
        status: _Types.Status
        tolerance_pct: Decimal | None

        @field_validator("tolerance_pct", mode="before")
        @classmethod
        def blank_tolerance(cls, value: str | None) -> str | None:
            """Read an empty override as no override."""
            return value or None

    class Draft(BaseModel):
        """A new vendor as entered; the id, status and PAN are derived on creation."""

        legal_name: str
        country: str
        tax_id: str
        bank_account: str
        bank_code: str
        bank_name: str
        currency: str
        tolerance_pct: Decimal | None

    class Record(BaseModel):
        """The vendor fields identification needs."""

        vendor_id: str
        legal_name: str
        tax_key: str
        account_key: str
        bank_code_key: str
        status: _Types.Status


class VendorTypes:
    """Public vendor types."""

    type Status = _Types.Status
    Draft = _Types.Draft
    Record = _Types.Record


class _Helper:
    """Check a new vendor's form."""

    VENDOR_ID: Final = re.compile(r"V(\d+)")
    COUNTRY: Final = re.compile(r"[A-Z]{2}")
    CURRENCY: Final = re.compile(r"[A-Z]{3}")
    MAX_TOLERANCE: Final = Decimal(20)


class VendorEntityClass:
    """Own the vendor master."""

    def __init__(self, db_adapter: AdapterTypes.DB, *, rules_entity: RulesEntityClass) -> None:
        """Keep the database, the GSTIN rule and a creation lock."""
        self._db_adapter = db_adapter
        self._rules_entity = rules_entity
        self._create_lock = asyncio.Lock()

    def reset_in(self, session: Session, rows: list[dict[str, str]]) -> dict[str, str]:
        """Replace the vendor master with the seed rows inside the caller's transaction; return id to legal name."""
        session.execute(delete(VendorDB.Vendors))
        seeds = [_Types.Vendor.model_validate(row) for row in rows]
        session.add_all(VendorDB.Vendors(**seed.model_dump()) for seed in seeds)
        return {seed.vendor_id: seed.legal_name for seed in seeds}

    async def vendors(self) -> list[_Types.Vendor]:
        """Return every vendor, ordered by id."""
        query = select(VendorDB.Vendors).order_by(VendorDB.Vendors.vendor_id)
        return await self._db_adapter.run(
            lambda session: [_Types.Vendor.model_validate(row, from_attributes=True) for row in session.scalars(query)]
        )

    async def vendor(self, vendor_id: str) -> _Types.Vendor | None:
        """Return one vendor, or None when the id is unknown."""

        def load(session: Session) -> _Types.Vendor | None:
            row = session.get(VendorDB.Vendors, vendor_id)
            return None if row is None else _Types.Vendor.model_validate(row, from_attributes=True)

        return await self._db_adapter.run(load)

    async def create(self, draft: _Types.Draft) -> _Types.Vendor:
        """
        Add an approved vendor under the next free id; an Indian vendor's PAN is read from its GSTIN.

        Raises:
            MasterTypes.MasterError: invalid for a blank or malformed field; conflict when the tax ID or the bank
                account, normalised as identification compares them, already belongs to another vendor.

        """
        country, currency = draft.country.strip().upper(), draft.currency.strip().upper()
        tax_key, account_key = IdentityTypes.key(draft.tax_id), IdentityTypes.key(draft.bank_account)
        problem = self._problem(draft, country, currency, tax_key)
        if problem is not None:
            raise MasterTypes.MasterError("invalid", problem)

        def work(session: Session) -> _Types.Vendor:
            records = self._records_in(session)
            by_tax = next((record for record in records if IdentityTypes.same_tax_id(tax_key, record.tax_key)), None)
            if by_tax is not None:
                message = f"The tax ID {tax_key} already belongs to {by_tax.legal_name} ({by_tax.vendor_id})."
                raise MasterTypes.MasterError("conflict", message)

            bank_code_key = IdentityTypes.code_key(draft.bank_code)
            by_account = next(
                (
                    record
                    for record in records
                    if IdentityTypes.same_payee(account_key, bank_code_key, record.account_key, record.bank_code_key)
                ),
                None,
            )
            if by_account is not None:
                masked = IdentityTypes.masked(account_key)
                message = (
                    f"The bank account {masked} already belongs to {by_account.legal_name} ({by_account.vendor_id})."
                )
                raise MasterTypes.MasterError("conflict", message)

            numbers = (
                int(found.group(1)) for record in records if (found := _Helper.VENDOR_ID.fullmatch(record.vendor_id))
            )
            row = VendorDB.Vendors(
                vendor_id=f"V{max(numbers, default=0) + 1:03d}",
                legal_name=draft.legal_name.strip(),
                country=country,
                tax_id=tax_key if country == "IN" else draft.tax_id.strip(),
                pan=tax_key[2:12] if country == "IN" else "",
                bank_account=draft.bank_account.strip(),
                bank_code=draft.bank_code.strip().upper(),
                bank_name=draft.bank_name.strip(),
                currency=currency,
                email_domain="",
                payment_terms="",
                status="approved",
                tolerance_pct=draft.tolerance_pct,
            )
            session.add(row)
            return _Types.Vendor.model_validate(row, from_attributes=True)

        async with self._create_lock:
            return await self._db_adapter.run(work)

    def _problem(self, draft: _Types.Draft, country: str, currency: str, tax_key: str) -> str | None:
        """Return the first reason the form cannot become a vendor, or None."""
        tolerance = draft.tolerance_pct
        checks = (
            (not draft.legal_name.strip(), "The vendor name must not be blank."),
            (not _Helper.COUNTRY.fullmatch(country), "The country must be a two-letter ISO 3166 code, such as IN."),
            (country == "IN" and not tax_key, "The tax ID must not be blank for an Indian vendor."),
            (
                country == "IN" and not self._rules_entity.gstin_shaped(tax_key),
                f"The tax ID {tax_key} is not a well-formed GSTIN, which an Indian vendor must have.",
            ),
            (not IdentityTypes.key(draft.bank_account), "The bank account number must not be blank."),
            (
                not draft.bank_code.strip() and not IdentityTypes.IBAN.fullmatch(IdentityTypes.key(draft.bank_account)),
                "The bank code must not be blank unless the account is an IBAN.",
            ),
            (not draft.bank_name.strip(), "The bank name must not be blank."),
            (
                not _Helper.CURRENCY.fullmatch(currency),
                "The currency must be a three-letter ISO 4217 code, such as INR.",
            ),
            (
                tolerance is not None and not 0 < tolerance <= _Helper.MAX_TOLERANCE,
                "The tolerance must be more than 0 and at most 20 percent.",
            ),
        )
        return next((message for failed, message in checks if failed), None)

    async def seeded(self) -> bool:
        """Return whether the vendor master holds any vendor."""
        query = select(func.count()).select_from(VendorDB.Vendors)
        return bool(await self._db_adapter.run(lambda session: session.scalar(query)))

    async def tolerance_pct(self, vendor_id: str) -> Decimal | None:
        """Return the vendor's tolerance override, if it has one."""
        query = select(VendorDB.Vendors.tolerance_pct).where(VendorDB.Vendors.vendor_id == vendor_id)
        return await self._db_adapter.run(lambda session: session.scalar(query))

    async def names(self, vendor_ids: Collection[str]) -> dict[str, str]:
        """Return the legal name of each vendor, by id; unknown ids are absent."""
        query = select(VendorDB.Vendors.vendor_id, VendorDB.Vendors.legal_name).where(
            VendorDB.Vendors.vendor_id.in_(vendor_ids)
        )
        rows = await self._db_adapter.run(lambda session: session.execute(query).all())
        return {row.vendor_id: row.legal_name for row in rows}

    async def records(self) -> list[_Types.Record]:
        """Return every vendor with its comparison keys, for the vendor and bank stages."""
        return await self._db_adapter.run(self._records_in)

    @staticmethod
    def _records_in(session: Session) -> list[_Types.Record]:
        """Load every vendor with its comparison keys."""
        return [
            _Types.Record(
                vendor_id=vendor.vendor_id,
                legal_name=vendor.legal_name,
                tax_key=IdentityTypes.key(vendor.tax_id),
                account_key=IdentityTypes.key(vendor.bank_account),
                bank_code_key=IdentityTypes.code_key(vendor.bank_code),
                status=vendor.status,
            )
            for vendor in session.scalars(select(VendorDB.Vendors))
        ]
