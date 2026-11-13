"""Version 1 admin service: the sample scenario, the extraction mode and demo reset."""

import asyncio
import functools
import time
from typing import Final

from fastapi import HTTPException, status

from app.entities import EntitiesRegistry
from app.routers.admin.admin_models import AdminModels


class AdminServiceV1Class:
    """Serve the scenario and reset the demo, at most once per interval."""

    def __init__(self) -> None:
        """Capture the shared entities and start with no reset recorded."""
        self._pipeline_entity = EntitiesRegistry.Pipeline
        self._extraction_stage = EntitiesRegistry.ExtractionStage
        self._run_entity = EntitiesRegistry.Run
        self._seed_entity = EntitiesRegistry.Seed
        self._last_reset: float | None = None

        self._reset_interval: Final = 10.0

    async def samples(self) -> AdminModels.V1.Samples:
        """Return the scenario with each step's last result since the reset."""
        samples, states = await asyncio.gather(self._seed_entity.samples(), self._run_entity.sample_states())
        rows: list[AdminModels.V1.Sample] = []
        for sample in samples:
            latest = states.get(sample.id)
            ran = latest is not None and latest.status == "completed"
            rows.append(
                AdminModels.V1.Sample(**sample.model_dump(), ran=ran, result=latest.outcome if latest and ran else None)
            )

        return AdminModels.V1.Samples(data=rows)

    async def document(self, sample_id: str) -> bytes:
        """
        Return a sample invoice's PDF.

        Raises:
            HTTPException: 404 for an unknown sample.

        """
        sample = await self._seed_entity.sample(sample_id)
        if sample is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown sample {sample_id}")

        return sample.pdf

    def meta(self) -> AdminModels.V1.Meta:
        """Return the extraction mode and model."""
        return AdminModels.V1.Meta(mode=self._extraction_stage.mode, model=self._extraction_stage.model)

    async def reset(self) -> AdminModels.V1.Reset:
        """
        Restore samples, vendors and POs to the seed files and delete runs, the register and uploads.

        Raises:
            HTTPException: 429 when the previous reset was less than the interval ago.

        """
        now = time.monotonic()
        if self._last_reset is not None and now - self._last_reset < self._reset_interval:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Reset at most once every 10 seconds")

        self._last_reset = now
        await self._pipeline_entity.reset()
        return AdminModels.V1.Reset(ok=True)


@functools.cache
def get_admin_service_singleton_v1() -> AdminServiceV1Class:
    """Return the shared version 1 admin service."""
    return AdminServiceV1Class()
