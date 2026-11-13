"""Application entrypoint: lifespan wiring, CORS and router mounting."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters import AdapterRegistry
from app.config import ENVConfigs
from app.entities import EntitiesRegistry
from app.routers import AdminRouters, OrderRouters, RunRouters, VendorRouters

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Build the registries at startup; cancel runs and release the database at shutdown."""
    adapter = AdapterRegistry()
    await EntitiesRegistry.initialize(adapter)
    try:
        yield
    finally:
        await EntitiesRegistry.Pipeline.close()
        await adapter.close()


app = FastAPI(title="PS-1 Invoice Processing", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ENVConfigs.Storage.cors_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
for router in (*RunRouters, *OrderRouters, *VendorRouters, *AdminRouters):
    app.include_router(router)
