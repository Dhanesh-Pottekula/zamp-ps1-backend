"""Expose the versioned vendor routers."""

from app.routers.vendor.controllers.vendor_c_v1 import router_v1

VendorRouters = (router_v1,)

__all__ = ["VendorRouters"]
