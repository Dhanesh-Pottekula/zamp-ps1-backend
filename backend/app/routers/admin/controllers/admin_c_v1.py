"""Version 1 admin controller: the sample scenario, the extraction mode and demo reset."""

from fastapi import APIRouter, Response

from app.routers.admin.admin_models import AdminModels  # noqa: TC001 - FastAPI resolves parameter and response types at runtime
from app.routers.admin.config import AdminConfig  # noqa: TC001 - FastAPI resolves parameter and response types at runtime
from app.routers.admin.services import AdminServiceRegistry  # noqa: TC001 - FastAPI resolves parameter and response types at runtime

router_v1 = APIRouter(prefix="/v1/admin", tags=["Admins"])


@router_v1.get("/samples", summary="List the sample scenario.")
async def list_samples(service: AdminServiceRegistry.V1) -> AdminModels.V1.Samples:
    """Return the ordered samples with their last results."""
    return await service.samples()


@router_v1.get("/samples/{sample}/document", summary="Show a sample invoice.", response_class=Response)
async def get_document(service: AdminServiceRegistry.V1, sample: AdminConfig.SampleId) -> Response:
    """Return the sample's PDF for viewing in the browser, before or after it is run."""
    headers = {"Content-Disposition": f'inline; filename="{sample}.pdf"'}
    return Response(await service.document(sample), media_type="application/pdf", headers=headers)


@router_v1.get("/meta", summary="Show how documents are read.")
async def get_meta(service: AdminServiceRegistry.V1) -> AdminModels.V1.Meta:
    """Return the extraction mode (live or recorded) and the model."""
    return service.meta()


@router_v1.post("/reset", summary="Reset the demo data.")
async def reset_demo(service: AdminServiceRegistry.V1) -> AdminModels.V1.Reset:
    """Delete runs and uploads and restore samples, vendors and POs to the seed; at most once every 10 seconds."""
    return await service.reset()
