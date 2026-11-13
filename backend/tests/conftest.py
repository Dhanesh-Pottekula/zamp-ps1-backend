"""Test settings and fixtures: a temp database and upload folder, recorded mode, no network."""

import json
import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

_TEMP = Path(tempfile.mkdtemp(prefix="ps1-tests-"))
os.environ.update(
    {
        "STORAGE_DB_PATH": str(_TEMP / "test.sqlite3"),
        "STORAGE_UPLOAD_DIR": str(_TEMP / "uploads"),
        "LLM_MODE": "recorded",
        "LLM_PROVIDER": "anthropic",
        "LLM_REPLAY_DELAY": "0",
        "JEV_MODE": "recorded",
        "JEV_RECORD": "false",
    }
)
os.environ.pop("TYPESAFE_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

from app.adapters import AdapterRegistry  # noqa: E402 - settings must be in the environment before app imports
from app.entities import EntitiesRegistry, ExtractionTypes  # noqa: E402
from app.entities.rules_ent import RulesEntityClass  # noqa: E402
from app.entities.stages.amounts_stage import AmountsStageClass  # noqa: E402
from app.entities.stages.decision_stage import DecisionStageClass  # noqa: E402
from app.entities.stages.extraction_stage import _Helper as ExtractionHelper  # noqa: E402
from app.entities.stages.validation_stage import ValidationStageClass  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.admin.services import get_admin_service_singleton_v1  # noqa: E402
from app.routers.order.services import get_order_service_singleton_v1  # noqa: E402
from app.routers.run.services import get_run_service_singleton_v1  # noqa: E402
from app.routers.vendor.services import get_vendor_service_singleton_v1  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

DATA = Path(__file__).resolve().parents[1] / "data"


def reading(sample: str = "INV-001", **changes: object) -> ExtractionTypes.InvoiceRead:
    """Return a sample's recorded reading with some fields replaced."""
    raw = json.loads((DATA / "invoices" / f"{sample}.read.json").read_text())
    raw.update(changes)
    return ExtractionTypes.InvoiceRead.model_validate(raw)


def extraction(sample: str = "INV-001", **changes: object) -> ExtractionTypes.InvoiceExtraction:
    """Return a sample's normalised extraction with some printed fields replaced."""
    return ExtractionHelper.convert(reading(sample, **changes))


def line(**changes: str) -> dict[str, str]:
    """Return INV-001's printed line with some fields replaced."""
    base = {
        "description": "Laptop stand (adjustable aluminium)",
        "quantity": "30",
        "unit": "pcs",
        "unit_price": "1,200.00",
        "line_amount": "36,000.00",
        "tax_rate_pct": "18",
    }
    return base | changes


def pytest_sessionfinish() -> None:
    """Remove the temp database and uploads."""
    shutil.rmtree(_TEMP, ignore_errors=True)


def clear_services() -> None:
    """Drop cached services so they capture the current registry."""
    getters = (
        get_run_service_singleton_v1,
        get_order_service_singleton_v1,
        get_vendor_service_singleton_v1,
        get_admin_service_singleton_v1,
    )
    for getter in getters:
        getter.cache_clear()


class StageRules:
    """The AP policy and the three stages that apply it without I/O: validation, amounts and decision."""

    def __init__(self, policy: RulesEntityClass) -> None:
        self.policy = policy
        self.validation = ValidationStageClass(policy)
        self.amounts = AmountsStageClass(policy, None)  # type: ignore[arg-type] - the pure checks need no PO entity
        self.decision = DecisionStageClass(policy)


@pytest.fixture
def rules() -> StageRules:
    """The validation, amounts and decision stages over the default tolerance policy."""
    return StageRules(RulesEntityClass(Decimal("2.0"), {"INR": Decimal(500), "USD": Decimal(10)}))


@pytest.fixture
async def adapter() -> AsyncIterator[AdapterRegistry]:
    """The adapters on the test database."""
    adapters = AdapterRegistry()
    yield adapters
    await adapters.close()


@pytest.fixture
async def registry(adapter: AdapterRegistry) -> AsyncIterator[type[EntitiesRegistry]]:
    """Initialised entities on freshly reset seed data."""
    await EntitiesRegistry.initialize(adapter)
    await EntitiesRegistry.Pipeline.reset()
    yield EntitiesRegistry
    await EntitiesRegistry.Pipeline.close()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client on the app with its lifespan running and seed data freshly reset."""
    async with app.router.lifespan_context(app):
        await EntitiesRegistry.Pipeline.reset()
        clear_services()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http

    clear_services()
