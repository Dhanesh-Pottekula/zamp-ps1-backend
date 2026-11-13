"""Expose the grouped router tuples mounted by the application."""

from app.routers.admin.controllers import AdminRouters
from app.routers.order.controllers import OrderRouters
from app.routers.run.controllers import RunRouters
from app.routers.vendor.controllers import VendorRouters

__all__ = ["AdminRouters", "OrderRouters", "RunRouters", "VendorRouters"]
