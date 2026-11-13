"""The OpenAI adapter's request construction (stub client, no network) and the choice of provider."""

import base64
from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest
from openai.lib._parsing._responses import type_to_text_format_param

from app.adapters import ExtractionError
from app.adapters.openai_adp import OpenAIAdapterClass
from app.config.env_config import _LlmConfigClass
from app.entities import ExtractionTypes


class StubResponses:
    """Record each request and answer with the queued outcomes."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._outcomes = outcomes

    async def parse(self, **request: Any) -> Any:
        self.calls.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome

        return outcome


def response(status: str = "completed", parsed: object = "parsed", reason: str | None = None) -> SimpleNamespace:
    details = SimpleNamespace(cached_tokens=900, cache_write_tokens=100)
    usage = SimpleNamespace(input_tokens=2200, input_tokens_details=details, output_tokens=400)
    incomplete = SimpleNamespace(reason=reason) if reason else None
    return SimpleNamespace(status=status, output_parsed=parsed, incomplete_details=incomplete, usage=usage)


def live(outcomes: list[Any]) -> tuple[OpenAIAdapterClass, StubResponses]:
    responses = StubResponses(outcomes)
    client = SimpleNamespace(responses=responses)
    adapter = OpenAIAdapterClass("key", "gpt-6-luna", "low", 16000, client=client)  # type: ignore[arg-type]
    adapter._retry_delay = 0.0  # type: ignore[misc]
    return adapter, responses


async def extract(adapter: OpenAIAdapterClass, media_type: str = "application/pdf") -> Any:
    return await adapter.extract(
        b"%PDF-1.7 bytes",
        media_type=media_type,  # type: ignore[arg-type]
        system_prompt="SYSTEM",
        context_prompt="CONTEXT",
        response_model=ExtractionTypes.InvoiceRead,
    )


def failure(kind: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    return kind("failed", response=httpx2.Response(status, request=request), body=None)


def settings(**values: Any) -> _LlmConfigClass:
    """Build the settings from explicit values only, never from the env files or the environment."""
    explicit = {"mode": "auto", "provider": "auto", "ANTHROPIC_API_KEY": None, "OPENAI_API_KEY": None} | values
    return _LlmConfigClass(_env_file=None, **explicit)  # type: ignore[call-arg]


async def test_request_shape_and_usage() -> None:
    adapter, responses = live([response()])

    parsed, usage = await extract(adapter)

    request = responses.calls[0]
    encoded = base64.standard_b64encode(b"%PDF-1.7 bytes").decode()
    prefix, message = request["input"]
    document, context = message["content"]
    assert parsed == "parsed"
    assert usage.model_dump() == {"input": 1200, "cache_read": 900, "cache_write": 100, "output": 400}
    assert request["model"] == "gpt-6-luna"
    assert request["max_output_tokens"] == 16000
    assert request["reasoning"] == {"effort": "low"}
    assert request["text_format"] is ExtractionTypes.InvoiceRead
    assert request["store"] is False
    assert prefix == {
        "role": "developer",
        "content": [{"type": "input_text", "text": "SYSTEM", "prompt_cache_breakpoint": {"mode": "explicit"}}],
    }
    assert (request["prompt_cache_key"], request["prompt_cache_options"]) == ("invoice-extract", {"mode": "explicit"})
    assert message["role"] == "user"
    assert "instructions" not in request
    assert document == {
        "type": "input_file",
        "filename": "invoice.pdf",
        "file_data": f"data:application/pdf;base64,{encoded}",
    }
    assert context == {"type": "input_text", "text": "CONTEXT"}


async def test_images_go_in_an_image_block() -> None:
    adapter, responses = live([response()])

    await extract(adapter, "image/png")

    block = responses.calls[0]["input"][1]["content"][0]
    assert (block["type"], block["detail"]) == ("input_image", "auto")
    assert block["image_url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    "answer",
    [response("incomplete", reason="max_output_tokens"), response(parsed=None), response("failed", parsed=None)],
)
async def test_incomplete_answers_raise(answer: SimpleNamespace) -> None:
    adapter, _ = live([answer])

    with pytest.raises(ExtractionError):
        await extract(adapter)


async def test_one_retry_then_success() -> None:
    adapter, responses = live([failure(openai.RateLimitError, 429), response()])

    parsed, _ = await extract(adapter)

    assert (parsed, len(responses.calls)) == ("parsed", 2)


async def test_a_second_failure_and_other_statuses_raise() -> None:
    twice, calls = live([failure(openai.InternalServerError, 500), failure(openai.InternalServerError, 500)])
    refused, once = live([failure(openai.BadRequestError, 400)])

    with pytest.raises(ExtractionError, match="after one retry"):
        await extract(twice)
    with pytest.raises(ExtractionError, match="status 400"):
        await extract(refused)

    assert (len(calls.calls), len(once.calls)) == (2, 1)


def test_the_invoice_schema_is_accepted_as_a_strict_format() -> None:
    text_format = type_to_text_format_param(ExtractionTypes.InvoiceRead)

    assert (text_format["type"], text_format["strict"]) == ("json_schema", True)
    assert set(text_format["schema"]["required"]) == set(text_format["schema"]["properties"])


def test_no_key_stays_recorded() -> None:
    config = settings()

    assert (config.active, config.chosen, config.chosen_model) == ("recorded", "anthropic", "claude-opus-5-5")


def test_an_openai_key_alone_chooses_openai() -> None:
    config = settings(OPENAI_API_KEY="sk-test")

    assert (config.active, config.chosen, config.chosen_model) == ("live", "openai", "gpt-6-luna")
    assert config.chosen_key == "sk-test"


def test_anthropic_wins_when_both_keys_are_set_unless_the_provider_is_forced() -> None:
    both = settings(ANTHROPIC_API_KEY="sk-ant-test", OPENAI_API_KEY="sk-test")
    forced = settings(ANTHROPIC_API_KEY="sk-ant-test", OPENAI_API_KEY="sk-test", provider="openai")

    assert (both.chosen, both.chosen_key) == ("anthropic", "sk-ant-test")
    assert (forced.chosen, forced.chosen_key, forced.active) == ("openai", "sk-test", "live")


def test_a_forced_provider_without_its_key_stays_recorded() -> None:
    config = settings(ANTHROPIC_API_KEY="sk-ant-test", provider="openai")

    assert (config.chosen, config.active) == ("openai", "recorded")
