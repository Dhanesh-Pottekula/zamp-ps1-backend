"""File storage: uploaded documents under the upload folder and seed files under the data folder."""

import asyncio
import shutil
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

    from app.adapters._types import LlmTypesClass


class StoreAdapterClass:
    """Save and read files without blocking the event loop."""

    SUFFIXES: Final[dict[LlmTypesClass.MediaType, str]] = {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
    }

    def __init__(self, upload_dir: Path, data_dir: Path) -> None:
        """Remember the two roots."""
        self._upload_dir: Final = upload_dir
        self._data_dir: Final = data_dir

    async def save(self, folder: str, media_type: LlmTypesClass.MediaType, data: bytes) -> Path:
        """Write an accepted document to <upload_dir>/<folder>/document.<ext>; the uploaded name never reaches disk."""
        target = self._upload_dir / folder / f"document{self.SUFFIXES[media_type]}"

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        await asyncio.to_thread(write)
        return target

    async def find(self, folder: str) -> tuple[Path, LlmTypesClass.MediaType] | None:
        """
        Return the document stored under <upload_dir>/<folder> with its media type, or None when there is none.

        A folder name that is not purely letters and digits is refused, so no name can reach outside the upload folder.
        """
        if not folder.isalnum():
            return None

        def look() -> tuple[Path, LlmTypesClass.MediaType] | None:
            for media_type, suffix in self.SUFFIXES.items():
                target = self._upload_dir / folder / f"document{suffix}"
                if target.is_file():
                    return target, media_type

            return None

        return await asyncio.to_thread(look)

    async def read_data(self, relative: str) -> bytes:
        """Return the bytes of a file under the data folder."""
        return await asyncio.to_thread((self._data_dir / relative).read_bytes)

    async def clear_uploads(self) -> None:
        """Delete every stored upload."""
        await asyncio.to_thread(shutil.rmtree, self._upload_dir, ignore_errors=True)
