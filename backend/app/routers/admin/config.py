"""Shared path contracts for the admin router."""

from typing import Annotated

from fastapi import Path


class AdminConfig:
    """Parameter contracts reused by admin endpoints."""

    type SampleId = Annotated[str, Path(pattern=r"^INV-\d{3}$")]
