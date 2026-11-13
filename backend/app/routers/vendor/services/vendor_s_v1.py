"""Version 1 vendor service: list the vendor master and add a vendor."""

import functools
from typing import Final

from fastapi import HTTPException, status

from app.entities import EntitiesRegistry, MasterTypes, VendorTypes
from app.routers.vendor.vendor_models import VendorModels


class VendorServiceV1Class:
    """Read and extend the vendor master through the vendor entity."""

    def __init__(self) -> None:
        """Capture the shared vendor entity and the status of each refusal."""
        self._vendor_entity = EntitiesRegistry.Vendor

        self._statuses: Final[dict[MasterTypes.Reason, int]] = {
            "invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "conflict": status.HTTP_409_CONFLICT,
        }

    async def listing(self) -> VendorModels.V1.Vendors:
        """Return every vendor, ordered by id."""
        vendors = await self._vendor_entity.vendors()
        return VendorModels.V1.Vendors.model_validate({"data": [vendor.model_dump() for vendor in vendors]})

    async def create(self, draft: VendorModels.V1.NewVendor) -> VendorModels.V1.Vendor:
        """
        Add an approved vendor with the next free id.

        Raises:
            HTTPException: 422 for a blank or malformed field; 409 when the tax ID or bank account is already taken.

        """
        try:
            vendor = await self._vendor_entity.create(VendorTypes.Draft.model_validate(draft.model_dump()))
        except MasterTypes.MasterError as error:
            raise HTTPException(self._statuses[error.reason], str(error)) from error

        return VendorModels.V1.Vendor.model_validate(vendor.model_dump())


@functools.cache
def get_vendor_service_singleton_v1() -> VendorServiceV1Class:
    """Return the shared version 1 vendor service."""
    return VendorServiceV1Class()
