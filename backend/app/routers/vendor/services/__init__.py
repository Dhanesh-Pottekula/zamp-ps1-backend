"""Expose dependency-injected vendor services."""

from typing import Annotated

from fastapi import Depends

from app.routers.vendor.services.vendor_s_v1 import VendorServiceV1Class, get_vendor_service_singleton_v1


class VendorServiceRegistry:
    """FastAPI dependency aliases for vendor service versions."""

    type V1 = Annotated[VendorServiceV1Class, Depends(get_vendor_service_singleton_v1)]


__all__ = ["VendorServiceRegistry", "get_vendor_service_singleton_v1"]
