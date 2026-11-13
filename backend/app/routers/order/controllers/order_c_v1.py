"""Version 1 order controller: PO balances and new purchase orders."""

from fastapi import APIRouter, status

from app.routers.order.order_models import OrderModels  # noqa: TC001 - FastAPI resolves parameter and response types at runtime
from app.routers.order.services import OrderServiceRegistry  # noqa: TC001 - FastAPI resolves parameter and response types at runtime

router_v1 = APIRouter(prefix="/v1/orders", tags=["Orders"])


@router_v1.get("", summary="List PO balances.")
async def list_orders(service: OrderServiceRegistry.V1) -> OrderModels.V1.Orders:
    """Return every PO with its total, used and remaining amounts."""
    return await service.balances()


@router_v1.post("", status_code=status.HTTP_201_CREATED, summary="Create a purchase order.")
async def create_order(service: OrderServiceRegistry.V1, draft: OrderModels.V1.NewOrder) -> OrderModels.V1.Order:
    """Raise an open PO numbered PO-YYYY-NNNN; 422 for bad lines, 404 for an unknown vendor, 409 if suspended."""
    return await service.create(draft)
