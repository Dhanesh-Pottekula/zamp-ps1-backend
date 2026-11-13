"""The InvoiceRead wire schema and the live adapter's request construction (stub client, no network)."""

import base64
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import anthropic
import httpx2
import pytest
from conftest import DATA
from pydantic import ValidationError

from app.adapters import ExtractionError
from app.adapters.llm_adp import AnthropicAdapterClass
from app.entities import ExtractionTypes

if TYPE_CHECKING:
    from pathlib import Path


def objects(node: Any) -> list[dict[str, Any]]:
    """Return every object schema nested anywhere in node."""
    if isinstance(node, list):
        return [found for item in node for found in objects(item)]

    if not isinstance(node, dict):
        return []

    own = [node] if node.get("type") == "object" else []
    return own + [found for value in node.values() for found in objects(value)]


def test_invoice_read_schema_is_closed_and_fully_required() -> None:
    schema = ExtractionTypes.InvoiceRead.model_json_schema()
    found = objects(schema)

    assert "anyOf" not in json.dumps(schema)
    assert len(found) == 4
    for node in found:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])
        assert all("default" not in prop for prop in node["properties"].values())


@pytest.mark.parametrize("recording", sorted((DATA / "invoices").glob("*.read.json")), ids=lambda path: path.name)
def test_recorded_readings_validate(recording: Path) -> None:
    ExtractionTypes.InvoiceRead.model_validate_json(recording.read_text())


class StubMessages:
    """Records parse() calls and replays scripted outcomes."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._outcomes = outcomes

    async def parse(self, **request: Any) -> Any:
        self.calls.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome

        return outcome


def response(stop_reason: str = "end_turn", parsed: object = "parsed") -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=1200, cache_read_input_tokens=900, cache_creation_input_tokens=0, output_tokens=400
    )
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed, usage=usage)


def live(outcomes: list[Any]) -> tuple[AnthropicAdapterClass, StubMessages]:
    messages = StubMessages(outcomes)
    client = SimpleNamespace(messages=messages)
    adapter = AnthropicAdapterClass("key", "claude-opus-5-5", "low", "1h", 16000, client=client)  # type: ignore[arg-type]
    return adapter, messages


async def extract(adapter: AnthropicAdapterClass, media_type: str = "application/pdf") -> Any:
    return await adapter.extract(
        b"%PDF-1.7 bytes",
        media_type=media_type,  # type: ignore[arg-type]
        system_prompt="SYSTEM",
        context_prompt="CONTEXT",
        response_model=ExtractionTypes.InvoiceRead,
    )


async def test_live_request_shape() -> None:
    adapter, messages = live([response()])

    parsed, usage = await extract(adapter)

    request = messages.calls[0]
    assert parsed == "parsed"
    assert usage.model_dump() == {"input": 1200, "cache_read": 900, "cache_write": 0, "output": 400}
    assert request["model"] == "claude-opus-5-5"
    assert request["max_tokens"] == 16000
    assert request["output_config"] == {"effort": "low"}
    assert request["output_format"] is ExtractionTypes.InvoiceRead
    assert request["system"] == [
        {"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    ]
    document, context = request["messages"][0]["content"]
    assert request["messages"][0]["role"] == "user"
    assert document == {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(b"%PDF-1.7 bytes").decode(),
        },
    }
    assert context == {"type": "text", "text": "CONTEXT"}
    assert not {"thinking", "temperature", "tool_choice"} & set(request)


async def test_images_go_in_an_image_block() -> None:
    adapter, messages = live([response()])

    await extract(adapter, "image/png")

    block = messages.calls[0]["messages"][0]["content"][0]
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_incomplete_answers_raise(stop_reason: str) -> None:
    adapter, _ = live([response(stop_reason)])

    with pytest.raises(ExtractionError):
        await extract(adapter)


async def test_missing_parsed_output_raises() -> None:
    adapter, _ = live([response(parsed=None)])

    with pytest.raises(ExtractionError):
        await extract(adapter)


async def test_unparseable_answer_raises() -> None:
    try:
        ExtractionTypes.InvoiceRead.model_validate_json('{"supplier_name": "Acme", "lines": [')
    except ValidationError as error:
        truncated = error

    adapter, _ = live([truncated])

    with pytest.raises(ExtractionError, match="schema"):
        await extract(adapter)


def request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


async def test_rate_limit_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr("app.adapters.llm_adp.asyncio.sleep", no_wait)
    limited = anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=request()), body=None)
    adapter, messages = live([limited, response()])

    parsed, _ = await extract(adapter)

    assert parsed == "parsed"
    assert len(messages.calls) == 2


async def test_second_connection_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr("app.adapters.llm_adp.asyncio.sleep", no_wait)
    down = anthropic.APIConnectionError(request=request())
    adapter, messages = live([down, anthropic.APIConnectionError(request=request())])

    with pytest.raises(ExtractionError):
        await extract(adapter)

    assert len(messages.calls) == 2


async def test_status_errors_carry_the_status() -> None:
    failed = anthropic.APIStatusError("bad", response=httpx2.Response(400, request=request()), body=None)
    adapter, messages = live([failed])

    with pytest.raises(ExtractionError, match="400"):
        await extract(adapter)

    assert len(messages.calls) == 1
