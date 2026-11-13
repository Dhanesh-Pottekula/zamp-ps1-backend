"""The real TypeSafe adapter and SDK against a fake TypeSafe server: what Jev receives and how its answers are read."""

import json
from typing import Any

import httpx2
import pytest
from test_assist import balances, cordless, run

from app.adapters.typesafe_adp import TypeSafeAdapterClass
from app.entities import EntitiesRegistry


def server(answers: dict[str, Any], seen: list[dict[str, Any]], status: int = 200) -> httpx2.MockTransport:
    """A fake TypeSafe API that records each request body and answers every question from answers."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": body})
        if status != 200:
            return httpx2.Response(status, json={"detail": "down"})

        return httpx2.Response(
            200,
            json={
                "model": body["model"],
                "answers": {name: {"type": "noul", "noul": answers.get(name, 0.02)} for name in body["questions"]},
                "usage": {"input_tokens": 120, "output_tokens": 4},
            },
        )

    return httpx2.MockTransport(handle)


QUESTIONS = {
    "po_PO-2024-0162": {
        "type": "noul",
        "instructions": "Does this invoice bill items ordered on PO-2024-0162?",
        "criteria": {"true": "yes", "false": "no"},
    },
    "po_PO-2024-0163": {"type": "noul", "instructions": "Does this invoice bill items ordered on PO-2024-0163?"},
}


async def test_the_adapter_sends_state_questions_and_model_and_reads_each_probability() -> None:
    seen: list[dict[str, Any]] = []
    transport = server({"po_PO-2024-0162": 0.96, "po_PO-2024-0163": 0.12}, seen)
    adapter = TypeSafeAdapterClass("ts-test-key", "jev-1.13.0", transport=transport)

    answers = await adapter.evaluate({"invoice_lines": [{"line": 1}]}, QUESTIONS, timeout=10.0)
    await adapter.close()

    assert answers == {"po_PO-2024-0162": 0.96, "po_PO-2024-0163": 0.12}
    request = seen[0]
    assert request["body"]["model"] == "jev-1.13.0"
    assert request["body"]["state"] == {"invoice_lines": [{"line": 1}]}
    assert set(request["body"]["questions"]) == set(QUESTIONS)
    assert request["body"]["questions"]["po_PO-2024-0162"]["criteria"] == {"true": "yes", "false": "no"}
    assert request["auth"] is not None and "ts-test-key" in request["auth"]
    assert len(seen) == 1  # retries are off


async def test_a_server_error_raises_the_sdk_error_once() -> None:
    seen: list[dict[str, Any]] = []
    adapter = TypeSafeAdapterClass("ts-test-key", "jev-1.13.0", transport=server({}, seen, status=503))

    with pytest.raises(TypeSafeAdapterClass.Error):
        await adapter.evaluate({}, QUESTIONS, timeout=10.0)
    await adapter.close()

    assert len(seen) == 1


async def test_a_not_sure_line_goes_through_the_real_adapter_end_to_end(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    answers = {"line_1__PO-2024-0159__4": 0.97}
    adapter = TypeSafeAdapterClass("ts-test-key", "jev-1.13.0", transport=server(answers, seen))
    monkeypatch.setattr(EntitiesRegistry.Jev, "_jev_adapter", adapter)
    before = await balances(client)

    detail = await run(client, cordless())
    await adapter.close()

    assert detail["outcome"] == "approved"
    assert len(seen) == 1
    state = seen[0]["body"]["state"]
    assert [line["description"] for line in state["invoice"]["lines"]] == [
        "Cordless headset",
        "Height-adjustable office desk",
    ]
    assert [order["po_number"] for order in state["purchase_orders"]] == [
        "PO-2024-0161",
        "PO-2024-0160",
        "PO-2024-0159",
    ]
    assert state["invoice"]["invoice_number"] == "BLI/24-25/0431"  # the whole invoice, as read
    assert "bank" not in json.dumps(state)
    after = await balances(client)
    assert float(before["PO-2024-0159"]) - float(after["PO-2024-0159"]) == 64000
    assert float(before["PO-2024-0160"]) - float(after["PO-2024-0160"]) == 135000
