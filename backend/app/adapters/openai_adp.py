"""
Live document reading through the OpenAI Responses API (the only importer of openai).

A prompt cache matches a request from its first byte up to a breakpoint, so the request is ordered from what never
changes to what always changes: the system prompt first, closed by the one breakpoint, then the document and its
context. Every invoice then reads the same cached prefix, and no invoice is written to the cache.
"""

import asyncio
import base64
from typing import TYPE_CHECKING, Any, Final, Literal

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, InternalServerError, RateLimitError
from pydantic import BaseModel, ValidationError

from app.adapters._types import ExtractionError, LlmTypesClass

if TYPE_CHECKING:
    from openai.types.responses import ParsedResponse


class _Helper:
    """Build the request pieces for one extraction call."""

    CACHE_KEY: Final = "invoice-extract"

    @staticmethod
    def shared_prefix(system_prompt: str) -> dict[str, Any]:
        """Return the developer message every call starts with; its breakpoint ends the cached prefix."""
        block = {"type": "input_text", "text": system_prompt, "prompt_cache_breakpoint": {"mode": "explicit"}}
        return {"role": "developer", "content": [block]}

    @staticmethod
    def document_block(document: bytes, media_type: LlmTypesClass.MediaType) -> dict[str, Any]:
        """Return the file block for a PDF, or the image block for a PNG / JPEG, as a base64 data URL."""
        data = f"data:{media_type};base64,{base64.standard_b64encode(document).decode()}"
        if media_type == "application/pdf":
            return {"type": "input_file", "filename": "invoice.pdf", "file_data": data}

        return {"type": "input_image", "image_url": data, "detail": "auto"}


class OpenAIAdapterClass:
    """Read one document into a structured model with a single cached-prompt call that OpenAI does not store."""

    def __init__(
        self,
        api_key: str,
        model: str,
        effort: Literal["low", "medium", "high", "xhigh", "max"],
        max_tokens: int,
        *,
        client: AsyncOpenAI | None = None,
    ) -> None:
        """Create the client with retries disabled, so the one retry below is the only one."""
        self._client = client or AsyncOpenAI(api_key=api_key, max_retries=0)

        self._model: Final = model
        self._effort: Final = effort
        self._max_tokens: Final = max_tokens
        self._retry_delay: Final = 2.0

    async def extract[T: BaseModel](
        self,
        document: bytes,
        *,
        media_type: LlmTypesClass.MediaType,
        system_prompt: str,
        context_prompt: str,
        response_model: type[T],
    ) -> tuple[T, LlmTypesClass.Usage]:
        """
        Read the document and return the parsed model with token usage.

        Raises:
            ExtractionError: refusal, truncation, missing parsed output, an API status error, or a second
                rate-limit / server / connection failure.

        """
        request: dict[str, Any] = {
            "model": self._model,
            "max_output_tokens": self._max_tokens,
            "reasoning": {"effort": self._effort},
            "text_format": response_model,
            "input": [
                _Helper.shared_prefix(system_prompt),
                {
                    "role": "user",
                    "content": [
                        _Helper.document_block(document, media_type),
                        {"type": "input_text", "text": context_prompt},
                    ],
                },
            ],
            # One key routes every call to the same cache; explicit mode writes the prefix only, never an invoice.
            "prompt_cache_key": _Helper.CACHE_KEY,
            "prompt_cache_options": {"mode": "explicit"},
            "store": False,
        }

        try:
            response = await self._send(request)
        except RateLimitError, InternalServerError, APIConnectionError:
            await asyncio.sleep(self._retry_delay)
            try:
                response = await self._send(request)
            except (RateLimitError, InternalServerError, APIConnectionError) as error:
                message = f"The model could not be reached after one retry: {type(error).__name__}."
                raise ExtractionError(message) from error

        if response.status != "completed" or response.output_parsed is None:
            reason = response.incomplete_details.reason if response.incomplete_details else None
            message = f"The model did not return a complete reading (status {response.status}, reason {reason})."
            raise ExtractionError(message)

        # OpenAI counts cached tokens inside input_tokens; Usage keeps them apart, as the Anthropic adapter does.
        counted = response.usage
        cache_read = counted.input_tokens_details.cached_tokens if counted else 0
        cache_write = counted.input_tokens_details.cache_write_tokens if counted else 0
        usage = LlmTypesClass.Usage(
            input=max(counted.input_tokens - cache_read - cache_write, 0) if counted else 0,
            cache_read=cache_read,
            cache_write=cache_write,
            output=counted.output_tokens if counted else 0,
        )
        return response.output_parsed, usage

    async def _send(self, request: dict[str, Any]) -> ParsedResponse[Any]:
        """
        Make one call; rate-limit, server and connection errors propagate for the caller's retry.

        Raises:
            ExtractionError: any other API status error, carrying the status code, or an answer that does not
                validate against the requested model (truncated or refused text).

        """
        try:
            return await self._client.responses.parse(**request)
        except RateLimitError, InternalServerError, APIConnectionError:
            raise
        except APIStatusError as error:
            message = f"The model API returned status {error.status_code}."
            raise ExtractionError(message) from error
        except ValidationError as error:
            message = (
                "The model's answer did not match the invoice schema; a cut-off or a refusal are the usual causes."
            )
            raise ExtractionError(message) from error
