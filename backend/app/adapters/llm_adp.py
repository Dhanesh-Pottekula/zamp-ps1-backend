"""Live document reading through the Anthropic Messages API (the only importer of anthropic)."""

import asyncio
import base64
from typing import TYPE_CHECKING, Any, Final, Literal

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic, InternalServerError, RateLimitError
from pydantic import BaseModel, ValidationError

from app.adapters._types import ExtractionError, LlmTypesClass

if TYPE_CHECKING:
    from anthropic.types.parsed_message import ParsedMessage


class _Helper:
    """Build the request pieces for one extraction call."""

    @staticmethod
    def document_block(document: bytes, media_type: LlmTypesClass.MediaType) -> dict[str, Any]:
        """Return the base64 document block for a PDF, or the image block for a PNG / JPEG."""
        source = {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(document).decode()}
        return {"type": "document" if media_type == "application/pdf" else "image", "source": source}


class AnthropicAdapterClass:
    """Read one document into a structured model with a single cached-prompt call."""

    def __init__(
        self,
        api_key: str,
        model: str,
        effort: Literal["low", "medium", "high", "xhigh", "max"],
        cache_ttl: Literal["5m", "1h"],
        max_tokens: int,
        *,
        client: AsyncAnthropic | None = None,
    ) -> None:
        """Create the client with retries disabled, so the one retry below is the only one."""
        self._client = client or AsyncAnthropic(api_key=api_key, max_retries=0)

        self._model: Final = model
        self._effort: Final = effort
        self._cache_ttl: Final = cache_ttl
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
            "max_tokens": self._max_tokens,
            "output_config": {"effort": self._effort},
            "output_format": response_model,
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral", "ttl": self._cache_ttl},
                }
            ],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        _Helper.document_block(document, media_type),
                        {"type": "text", "text": context_prompt},
                    ],
                }
            ],
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

        if response.stop_reason in {"refusal", "max_tokens"} or response.parsed_output is None:
            message = f"The model did not return a complete reading (stop reason {response.stop_reason})."
            raise ExtractionError(message)

        usage = LlmTypesClass.Usage(
            input=response.usage.input_tokens,
            cache_read=response.usage.cache_read_input_tokens or 0,
            cache_write=response.usage.cache_creation_input_tokens or 0,
            output=response.usage.output_tokens,
        )
        return response.parsed_output, usage

    async def _send(self, request: dict[str, Any]) -> ParsedMessage[Any]:
        """
        Make one call; rate-limit, server and connection errors propagate for the caller's retry.

        Raises:
            ExtractionError: any other API status error, carrying the status code, or an answer that does not
                validate against the requested model (truncated or refused text).

        """
        try:
            return await self._client.messages.parse(**request)
        except RateLimitError, InternalServerError, APIConnectionError:
            raise
        except APIStatusError as error:
            message = f"The model API returned status {error.status_code}."
            raise ExtractionError(message) from error
        except ValidationError as error:
            message = (
                "The model's answer did not match the invoice schema (stop reason unknown: the SDK validates before "
                "returning; a max_tokens cut-off or a refusal are the usual causes)."
            )
            raise ExtractionError(message) from error
