"""Expose dependency-injected order services."""

from typing import Annotated

from fastapi import Depends

from app.routers.order.services.order_s_v1 import OrderServiceV1Class, get_order_service_singleton_v1


class OrderServiceRegistry:
    """FastAPI dependency aliases for order service versions."""

    type V1 = Annotated[OrderServiceV1Class, Depends(get_order_service_singleton_v1)]


__all__ = ["OrderServiceRegistry", "get_order_service_singleton_v1"]
