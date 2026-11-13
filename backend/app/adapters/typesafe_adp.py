"""
TypeSafe AI adapter: typed yes/no evaluation requests against the Jev model (the only importer of typesafe_sdk).

Jev is an evaluation model, not a language model: it returns a probability per question instead of text, so it is
kept apart from the document-reading adapters.
"""

from typing import TYPE_CHECKING

import typesafe_sdk

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx2


class _Types:
    """The SDK aliases the adapter exposes."""

    type JSONValue = typesafe_sdk.JSONValue
    type Question = typesafe_sdk.Question
    Noul = typesafe_sdk.Noul
    Response = typesafe_sdk.SystemOneResponse
    Error = typesafe_sdk.TypeSafeError


class TypeSafeAdapterClass:
    """Evaluate yes/no questions with TypeSafe AI's Jev model; one request per call, never retried."""

    type JSONValue = _Types.JSONValue
    type Question = _Types.Question

    Noul = _Types.Noul
    Response = _Types.Response
    Error = _Types.Error

    def __init__(self, api_key: str, model: str, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        """
        Build the async client for one model with retries off; the timeout is given per call.

        transport replaces the network, for tests that run the real SDK against a fake TypeSafe server.
        """
        self._client = typesafe_sdk.AsyncTypeSafeClient(
            api_key=api_key, model=model, retry=typesafe_sdk.RetryPolicy(max_retries=0), transport=transport
        )

    async def evaluate(
        self,
        state: Mapping[str, _Types.JSONValue],
        questions: Mapping[str, _Types.Question],
        *,
        timeout: float,  # noqa: ASYNC109 - forwarded to the SDK's per-call HTTP budget, not a wrapper.
    ) -> dict[str, float]:
        """
        Ask named yes/no questions against one state in a single Jev request.

        Returns:
            The probability of yes per question name.

        Raises:
            typesafe_sdk.TypeSafeError: When the request fails, times out or the API rejects it.

        """
        response = await self._client.system_one(dict(state), dict(questions), timeout=timeout)
        return {name: answer.noul for name, answer in response.nouls.items()}

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
