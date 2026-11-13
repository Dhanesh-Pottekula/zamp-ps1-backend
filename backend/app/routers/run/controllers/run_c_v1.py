"""Version 1 run controller: start (upload, sample or typed invoice), list, inspect, view and follow invoice runs."""

from typing import Annotated

from fastapi import APIRouter, File, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.sse import EventSourceResponse

from app.routers.run.config import RunConfig  # noqa: TC001 - FastAPI resolves parameter and response types at runtime
from app.routers.run.run_models import RunModels  # noqa: TC001 - FastAPI resolves parameter and response types at runtime
from app.routers.run.services import RunServiceRegistry  # noqa: TC001 - FastAPI resolves parameter and response types at runtime

router_v1 = APIRouter(prefix="/v1/runs", tags=["Runs"])


@router_v1.post("", status_code=status.HTTP_202_ACCEPTED, summary="Process an uploaded invoice.")
async def create_run(service: RunServiceRegistry.V1, file: Annotated[UploadFile, File()]) -> RunModels.V1.Created:
    """Accept a PDF, PNG or JPEG and start processing it in the background."""
    return await service.upload(file)


@router_v1.post("/samples/{sample}", status_code=status.HTTP_202_ACCEPTED, summary="Process a sample invoice.")
async def create_sample_run(service: RunServiceRegistry.V1, sample: RunConfig.SampleId) -> RunModels.V1.Created:
    """Start a run on one of the scenario's sample invoices."""
    return await service.sample(sample)


@router_v1.post("/manual", status_code=status.HTTP_202_ACCEPTED, summary="Process a typed invoice.")
async def create_manual_run(service: RunServiceRegistry.V1, invoice: RunModels.V1.Manual) -> RunModels.V1.Created:
    """Start a run on an invoice a person typed; no model is called and every rule applies."""
    return await service.manual(invoice)


@router_v1.get("", summary="List runs.")
async def list_runs(service: RunServiceRegistry.V1, outcome: RunConfig.OutcomeFilter = None) -> RunModels.V1.Runs:
    """Return run history, newest first."""
    return await service.history(outcome)


@router_v1.get("/{run_id}", summary="Get one run.")
async def get_run(service: RunServiceRegistry.V1, run_id: RunConfig.RunId) -> RunModels.V1.Detail:
    """Return a run with its ordered reasons and steps."""
    return await service.detail(run_id)


@router_v1.get("/{run_id}/document", summary="Show a run's invoice.", response_class=FileResponse)
async def get_document(service: RunServiceRegistry.V1, run_id: RunConfig.RunId) -> FileResponse:
    """Return the uploaded PDF or image with no download header, so the browser shows it; 404 when none is stored."""
    path, media_type = await service.document(run_id)
    return FileResponse(path, media_type=media_type)


@router_v1.get("/{run_id}/events", summary="Follow a run's steps.")
async def follow_run(service: RunServiceRegistry.V1, run_id: RunConfig.RunId) -> EventSourceResponse:
    """Replay the stored steps, then stream live steps until the closing `done` event."""
    return EventSourceResponse(await service.events(run_id))
