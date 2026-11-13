"""Transport models for purchase order balances and a new purchase order."""

from datetime import date
from decimal import Decimal  # noqa: TC003 - pydantic resolves field types at runtime

from pydantic import Field

from app.entities import StageTypes  # noqa: TC001 - pydantic resolves field types at runtime
from app.routers.config import RouterBaseModel, RouterTypes


class _Types:
    """Parts of the order responses."""

    class Line(RouterBaseModel):
        """One PO line with what has been invoiced against it."""

        line_no: int = Field(serialization_alias="no")
        description: str = Field(serialization_alias="text")
        quantity: RouterTypes.Quantity = Field(serialization_alias="qty")
        consumed_qty: RouterTypes.Quantity = Field(serialization_alias="used")
        unit_price: RouterTypes.Money = Field(serialization_alias="price")
        tax_rate_pct: RouterTypes.Percent = Field(serialization_alias="tax")

    class DraftLine(RouterBaseModel):
        """One line of a new PO as entered; tax is a percent."""

        description: str = Field(validation_alias="text")
        quantity: Decimal = Field(validation_alias="qty")
        unit: str = "pcs"
        unit_price: Decimal = Field(validation_alias="price")
        tax_rate_pct: Decimal | None = Field(default=None, validation_alias="tax")


class OrderModels:
    """Versioned order transport contracts."""

    class V1:
        """Version 1 order contracts."""

        class Order(RouterBaseModel):
            """One PO with its balance."""

            po_number: str = Field(serialization_alias="po")
            vendor_name: str = Field(serialization_alias="vendor")
            currency: str
            status: StageTypes.OrderStatus
            total: RouterTypes.Money
            consumed: RouterTypes.Money = Field(serialization_alias="used")
            remaining: RouterTypes.Money = Field(serialization_alias="left")
            lines: list[_Types.Line]

        class NewOrder(RouterBaseModel):
            """A new PO as entered: the vendor, the PO date (today when absent) and 1 to 20 lines."""

            vendor_id: str
            po_date: date = Field(default_factory=date.today, validation_alias="date")
            lines: list[_Types.DraftLine]

        class Orders(RouterBaseModel):
            """Every PO with its balance."""

            data: list[OrderModels.V1.Order]
