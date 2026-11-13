"""
Stage 0, intake: accept the file or reject it, and store an accepted one.

Rejects a file over the size limit, one that is not a PDF, PNG or JPEG, one that cannot be opened, a
password-protected one, and one with more pages than the limit. A typed (manual) invoice skips this stage.
"""

from typing import Final

from app.adapters import AdapterTypes  # noqa: TC001 - the media type annotation is read at runtime by nothing else
from app.entities._types import FindingTypes, StageTypes


class IntakeStageClass:
    """Inspect, store or reject an uploaded document."""

    def __init__(
        self, pdf_adapter: AdapterTypes.Pdf, store_adapter: AdapterTypes.Store, *, max_bytes: int, max_pages: int
    ) -> None:
        """Keep the document inspector, the file store and the intake limits."""
        self._pdf_adapter = pdf_adapter
        self._store_adapter = store_adapter
        self.max_bytes: Final = max_bytes
        self._max_pages: Final = max_pages
        self._megabyte: Final = 1024 * 1024

    async def run(
        self, run_id: str, filename: str | None, data: bytes
    ) -> tuple[StageTypes.IntakeResult, AdapterTypes.MediaType | None]:
        """Inspect the file, store it, and reject it when it cannot be processed; return the media type kept."""
        size = len(data)
        kept: AdapterTypes.MediaType | None = None
        inspection = await self._pdf_adapter.inspect(data) if size <= self.max_bytes else None
        if inspection is None:
            reason = f"is larger than the {self.max_bytes // self._megabyte} MB limit"
        elif inspection.media_type is None:
            reason = "is not a PDF, PNG or JPEG"
        elif not inspection.readable:
            reason = "could not be opened"
        elif inspection.encrypted:
            reason = "is password-protected"
        elif inspection.page_count is not None and inspection.page_count > self._max_pages:
            reason = f"has {inspection.page_count} pages, over the {self._max_pages} page limit"
        else:
            reason = None
            kept = inspection.media_type
            await self._store_adapter.save(run_id, inspection.media_type, data)

        findings = (
            [
                FindingTypes.Finding(
                    code="FILE_REJECTED",
                    severity="block",
                    message=f"The file {reason}, so it cannot be processed.",
                    data={"size_bytes": str(size), "reason": reason},
                )
            ]
            if reason
            else []
        )
        result = StageTypes.IntakeResult(
            filename=filename,
            media_type=(inspection.media_type if inspection else None) or "unknown",
            size_bytes=size,
            page_count=inspection.page_count if inspection else None,
            encrypted=bool(inspection and inspection.encrypted),
            text_layer=inspection.text_layer if inspection else None,
            accepted=reason is None,
            findings=findings,
        )
        return result, kept
