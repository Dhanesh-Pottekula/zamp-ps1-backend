"""Version 1 order service: PO balances and new purchase orders."""

import functools
from typing import Final

from fastapi import HTTPException, status

from app.entities import EntitiesRegistry, MasterTypes, OrderTypes
from app.routers.order.order_models import OrderModels


class OrderServiceV1Class:
    """Read PO balances and raise POs through the order entity."""

    def __init__(self) -> None:
        """Capture the shared order entity and the status of each refusal."""
        self._order_entity = EntitiesRegistry.Order

        self._statuses: Final[dict[MasterTypes.Reason, int]] = {
            "invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "unknown": status.HTTP_404_NOT_FOUND,
            "conflict": status.HTTP_409_CONFLICT,
        }

    async def balances(self) -> OrderModels.V1.Orders:
        """Return every PO with its total, used and remaining amounts and its lines."""
        orders = await self._order_entity.snapshots()
        return OrderModels.V1.Orders.model_validate({"data": [order.model_dump() for order in orders]})

    async def create(self, draft: OrderModels.V1.NewOrder) -> OrderModels.V1.Order:
        """
        Raise an open PO for an approved vendor.

        Raises:
            HTTPException: 422 for a bad line or line count; 404 for an unknown vendor; 409 for a suspended vendor.

        """
        try:
            order = await self._order_entity.create(OrderTypes.Draft.model_validate(draft.model_dump()))
        except MasterTypes.MasterError as error:
            raise HTTPException(self._statuses[error.reason], str(error)) from error

        return OrderModels.V1.Order.model_validate(order.model_dump())


@functools.cache
def get_order_service_singleton_v1() -> OrderServiceV1Class:
    """Return the shared version 1 order service."""
    return OrderServiceV1Class()
