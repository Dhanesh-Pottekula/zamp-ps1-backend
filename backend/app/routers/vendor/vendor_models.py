"""Transport models for the vendor master: the list and a new vendor."""

from decimal import Decimal  # noqa: TC003 - pydantic resolves field types at runtime

from pydantic import Field

from app.entities import VendorTypes  # noqa: TC001 - pydantic resolves field types at runtime
from app.routers.config import RouterBaseModel, RouterTypes


class VendorModels:
    """Versioned vendor transport contracts."""

    class V1:
        """Version 1 vendor contracts."""

        class NewVendor(RouterBaseModel):
            """A new vendor as entered on the form; tolerance is a percent above 0 and at most 20."""

            legal_name: str = Field(validation_alias="name")
            country: str = "IN"
            tax_id: str
            bank_account: str = Field(validation_alias="account")
            bank_code: str
            bank_name: str = Field(validation_alias="bank")
            currency: str = "INR"
            tolerance_pct: Decimal | None = Field(default=None, validation_alias="tolerance")

        class Vendor(RouterBaseModel):
            """One vendor of the master."""

            vendor_id: str = Field(serialization_alias="id")
            legal_name: str = Field(serialization_alias="name")
            country: str
            tax_id: str
            bank_account: str = Field(serialization_alias="account")
            bank_code: str
            bank_name: str = Field(serialization_alias="bank")
            currency: str
            status: VendorTypes.Status
            tolerance_pct: RouterTypes.Percent | None = Field(serialization_alias="tolerance")

        class Vendors(RouterBaseModel):
            """Every vendor, ordered by id."""

            data: list[VendorModels.V1.Vendor]
