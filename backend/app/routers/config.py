"""Shared transport base model and wire formats for amounts, quantities and percentages."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer


class RouterBaseModel(BaseModel):
    """Base model for router request and response schemas."""

    model_config = ConfigDict(populate_by_name=True)


class RouterTypes:
    """Wire formats shared by every router."""

    type Money = Annotated[Decimal, PlainSerializer(lambda value: f"{value:.2f}", return_type=str)]
    type Quantity = Annotated[Decimal, PlainSerializer(lambda value: f"{value.normalize():f}", return_type=str)]
    type Percent = Quantity
