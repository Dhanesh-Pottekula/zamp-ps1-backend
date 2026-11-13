"""Shared path and query contracts for the run router."""

from typing import Annotated

from fastapi import Path, Query
from pydantic import BeforeValidator

from app.entities import StageTypes


class RunConfig:
    """Parameter contracts reused by run endpoints."""

    type RunId = Annotated[str, Path(max_length=64)]
    type SampleId = Annotated[str, Path(pattern=r"^INV-\d{3}$")]
    type OutcomeFilter = Annotated[StageTypes.Outcome | None, BeforeValidator(lambda value: value or None), Query()]
