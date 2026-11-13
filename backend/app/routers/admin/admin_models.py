"""Transport models for the demo scenario, the extraction mode and reset."""

from app.entities import StageTypes  # noqa: TC001 - pydantic resolves field types at runtime
from app.routers.config import RouterBaseModel


class AdminModels:
    """Versioned admin transport contracts."""

    class V1:
        """Version 1 admin contracts."""

        class Sample(RouterBaseModel):
            """One step of the scenario with its last result."""

            step: int
            id: str
            title: str
            expect: str
            outcome: StageTypes.Outcome
            ran: bool
            result: StageTypes.Outcome | None

        class Samples(RouterBaseModel):
            """The ordered scenario."""

            data: list[AdminModels.V1.Sample]

        class Meta(RouterBaseModel):
            """How documents are read."""

            mode: StageTypes.LlmMode
            model: str

        class Reset(RouterBaseModel):
            """Confirmation of a reset."""

            ok: bool
