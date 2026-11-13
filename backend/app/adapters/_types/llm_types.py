"""Types of the model adapter: media types, token usage and the extraction error."""

from typing import Literal

from pydantic import BaseModel


class LlmTypesClass:
    """Group the extraction adapter contract types."""

    type MediaType = Literal["application/pdf", "image/png", "image/jpeg"]

    class Usage(BaseModel):
        """Token counts of one extraction call."""

        input: int
        cache_read: int
        cache_write: int
        output: int


class ExtractionError(Exception):
    """Raised when a document could not be turned into the requested model."""
