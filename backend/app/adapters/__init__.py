"""
Adapter package: builds the shared adapter registry and exposes adapter-owned types.

The model adapter (Anthropic or OpenAI, whichever provider is chosen) exists only in live mode; in recorded mode
the extraction entity replays sample recordings from the database and no model adapter is built. Likewise the Jev
adapter exists only when TYPESAFE_API_KEY is set; otherwise PO selection replays recorded Jev answers.
"""

from app.adapters._types import ExtractionError, LlmTypesClass
from app.adapters.db_adp import Base, DBAdapterClass, DecimalText
from app.adapters.llm_adp import AnthropicAdapterClass
from app.adapters.match_adp import MatchAdapterClass
from app.adapters.openai_adp import OpenAIAdapterClass
from app.adapters.pdf_adp import PdfAdapterClass
from app.adapters.store_adp import StoreAdapterClass
from app.adapters.typesafe_adp import TypeSafeAdapterClass
from app.config import ENVConfigs


class AdapterTypes:
    """Expose adapter-owned types to the entity layer."""

    type MediaType = LlmTypesClass.MediaType
    Usage = LlmTypesClass.Usage
    type LLM = AnthropicAdapterClass | OpenAIAdapterClass
    DB = DBAdapterClass
    Jev = TypeSafeAdapterClass
    Match = MatchAdapterClass
    Pdf = PdfAdapterClass
    Store = StoreAdapterClass


class AdapterRegistry:
    """Construct every shared adapter from settings."""

    def __init__(self) -> None:
        """Build the adapters in alphabetical order; the model adapter only when the resolved LLM mode is live."""
        storage = ENVConfigs.Storage
        llm = ENVConfigs.Llm

        self.DB = DBAdapterClass(storage.db_path)
        jev = ENVConfigs.Jev
        self.Jev: TypeSafeAdapterClass | None = (
            TypeSafeAdapterClass(jev.key, jev.model) if jev.active == "live" else None
        )
        self.LLM: AdapterTypes.LLM | None = None
        if llm.active == "live" and llm.chosen == "openai":
            self.LLM = OpenAIAdapterClass(llm.chosen_key, llm.chosen_model, llm.effort, llm.max_tokens)
        elif llm.active == "live":
            self.LLM = AnthropicAdapterClass(
                llm.chosen_key, llm.chosen_model, llm.effort, llm.cache_ttl, llm.max_tokens
            )

        self.Match = MatchAdapterClass()
        self.Pdf = PdfAdapterClass()
        self.Store = StoreAdapterClass(storage.upload_dir, storage.data_dir)

    async def close(self) -> None:
        """Release long-lived resources."""
        await self.DB.close()
        if self.Jev is not None:
            await self.Jev.close()


__all__ = ["AdapterRegistry", "AdapterTypes", "Base", "DecimalText", "ExtractionError"]
