"""Expose dependency-injected admin services."""

from typing import Annotated

from fastapi import Depends

from app.routers.admin.services.admin_s_v1 import AdminServiceV1Class, get_admin_service_singleton_v1


class AdminServiceRegistry:
    """FastAPI dependency aliases for admin service versions."""

    type V1 = Annotated[AdminServiceV1Class, Depends(get_admin_service_singleton_v1)]


__all__ = ["AdminServiceRegistry", "get_admin_service_singleton_v1"]
