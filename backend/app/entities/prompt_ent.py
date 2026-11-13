"""Extraction prompt pair: loaded once at startup, the context half filled per document."""

import asyncio
from pathlib import Path
from typing import Final


class PromptEntityClass:
    """Hold the system prompt and the context template."""

    def __init__(self) -> None:
        """Locate the prompt folder; the texts load in initialize()."""
        self._prompt_dir: Final = Path(__file__).resolve().parent / "prompts"
        self.system = ""
        self._context = ""

    async def initialize(self) -> None:
        """Read both prompt files."""
        self.system, self._context = await asyncio.gather(
            asyncio.to_thread((self._prompt_dir / "system-extract.md").read_text, "utf-8"),
            asyncio.to_thread((self._prompt_dir / "context-extract.md").read_text, "utf-8"),
        )

    def context(self, filename: str | None) -> str:
        """Return the context prompt for one document; the file name line is left out when the upload had none."""
        lines = self._context.splitlines(keepends=True)
        kept = "".join(line for line in lines if filename is not None or "{{FILENAME}}" not in line)
        return kept.replace("{{FILENAME}}", filename or "")
