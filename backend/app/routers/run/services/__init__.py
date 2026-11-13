"""Expose dependency-injected run services."""

from typing import Annotated

from fastapi import Depends

from app.routers.run.services.run_s_v1 import RunServiceV1Class, get_run_service_singleton_v1


class RunServiceRegistry:
    """FastAPI dependency aliases for run service versions."""

    type V1 = Annotated[RunServiceV1Class, Depends(get_run_service_singleton_v1)]


__all__ = ["RunServiceRegistry", "get_run_service_singleton_v1"]
