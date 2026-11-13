"""Expose the versioned admin routers."""

from app.routers.admin.controllers.admin_c_v1 import router_v1

AdminRouters = (router_v1,)

__all__ = ["AdminRouters"]
