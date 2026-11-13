"""Expose the versioned order routers."""

from app.routers.order.controllers.order_c_v1 import router_v1

OrderRouters = (router_v1,)

__all__ = ["OrderRouters"]
