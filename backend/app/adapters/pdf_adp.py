"""Uploaded-document inspection (the only importer of pypdf)."""

import asyncio
import io
from typing import Final

from pydantic import BaseModel
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.adapters._types import LlmTypesClass  # noqa: TC001 - pydantic resolves the field type at runtime


class PdfTypesClass:
    """Group the inspection result type."""

    class Inspection(BaseModel):
        """What the bytes turned out to be; media_type is None for an unsupported type."""

        media_type: LlmTypesClass.MediaType | None
        readable: bool
        encrypted: bool
        page_count: int | None
        text_layer: bool | None


class _Helper:
    """Hold the file signatures and the blocking PDF read."""

    SIGNATURES: Final[tuple[tuple[bytes, LlmTypesClass.MediaType], ...]] = (
        (b"%PDF-", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
    )

    @staticmethod
    def read_pdf(data: bytes) -> PdfTypesClass.Inspection:
        """Open the PDF and report encryption, page count and whether any page has extractable text."""
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                return PdfTypesClass.Inspection(
                    media_type="application/pdf", readable=True, encrypted=True, page_count=None, text_layer=None
                )

            pages = list(reader.pages)
            text_layer = any(page.extract_text().strip() for page in pages)
        except PyPdfError, ValueError, KeyError, IndexError, TypeError, AttributeError:
            return PdfTypesClass.Inspection(
                media_type="application/pdf", readable=False, encrypted=False, page_count=None, text_layer=None
            )

        return PdfTypesClass.Inspection(
            media_type="application/pdf", readable=True, encrypted=False, page_count=len(pages), text_layer=text_layer
        )


class PdfAdapterClass:
    """Identify an uploaded document by its signature and inspect PDFs."""

    async def inspect(self, data: bytes) -> PdfTypesClass.Inspection:
        """Return the media type sniffed from the bytes and, for PDFs, the page facts (read off the event loop)."""
        media_type = next((kind for signature, kind in _Helper.SIGNATURES if data.startswith(signature)), None)
        if media_type == "application/pdf":
            return await asyncio.to_thread(_Helper.read_pdf, data)

        return PdfTypesClass.Inspection(
            media_type=media_type, readable=media_type is not None, encrypted=False, page_count=None, text_layer=None
        )
