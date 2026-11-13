"""Version 1 vendor controller: list and create vendors."""

from fastapi import APIRouter, status

from app.routers.vendor.services import VendorServiceRegistry  # noqa: TC001 - FastAPI resolves parameter and response types at runtime
from app.routers.vendor.vendor_models import VendorModels  # noqa: TC001 - FastAPI resolves parameter and response types at runtime

router_v1 = APIRouter(prefix="/v1/vendors", tags=["Vendors"])


@router_v1.get("", summary="List vendors.")
async def list_vendors(service: VendorServiceRegistry.V1) -> VendorModels.V1.Vendors:
    """Return every vendor of the master, ordered by id."""
    return await service.listing()


@router_v1.post("", status_code=status.HTTP_201_CREATED, summary="Create a vendor.")
async def create_vendor(service: VendorServiceRegistry.V1, draft: VendorModels.V1.NewVendor) -> VendorModels.V1.Vendor:
    """Add an approved vendor; 422 for a blank or malformed field, 409 for a tax ID or account already taken."""
    return await service.create(draft)
