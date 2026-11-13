"""Recorded mode from the database: sample PDFs and readings are seeded into the table and matched by content."""

import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest
from conftest import DATA
from sqlalchemy import event
from sqlalchemy.engine import Engine
from test_master import execute
from test_pipeline import stored, stream

from app.adapters.store_adp import StoreAdapterClass
from app.entities import EntitiesRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator

    import httpx


def seeded() -> list[tuple[Any, ...]]:
    """Return id, PDF bytes, digest and reading of every stored sample, in step order."""
    return stored("SELECT id, pdf, digest, reading FROM samples ORDER BY step")


def expected() -> list[tuple[str, bytes, str, dict[str, Any]]]:
    """Return what seeding must store for every sample of samples.json."""
    rows = []
    for sample in json.loads((DATA / "samples.json").read_text()):
        pdf = (DATA / "invoices" / f"{sample['id']}.pdf").read_bytes()
        reading = json.loads((DATA / "invoices" / f"{sample['id']}.read.json").read_text())
        rows.append((sample["id"], pdf, hashlib.sha256(pdf).hexdigest(), reading))

    return rows


@pytest.fixture
def statements() -> Iterator[list[str]]:
    """Collect every SQL statement any engine sends while the test runs."""
    sent: list[str] = []

    def collect(*args: Any) -> None:
        sent.append(args[2])

    event.listen(Engine, "before_cursor_execute", collect)
    yield sent
    event.remove(Engine, "before_cursor_execute", collect)


@pytest.fixture
def no_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every read of the seed folder fail from here on."""

    async def refuse(*_: object) -> bytes:
        message = "the seed folder was read after seeding"
        raise AssertionError(message)

    monkeypatch.setattr(StoreAdapterClass, "read_data", refuse)


async def test_samples_carry_pdf_digest_and_reading_after_a_reset(client: httpx.AsyncClient) -> None:
    execute("UPDATE samples SET pdf = x'00', digest = 'stale', reading = '{}'")

    reset = await client.post("/v1/admin/reset")

    assert reset.status_code == 200
    assert [(sample_id, pdf, digest, json.loads(reading)) for sample_id, pdf, digest, reading in seeded()] == expected()
    assert stored("SELECT count(DISTINCT digest) FROM samples") == [(46,)]


async def test_startup_seeds_pdfs_and_readings_into_an_empty_table(client: httpx.AsyncClient) -> None:
    execute("DELETE FROM samples")

    await EntitiesRegistry.Seed.initialize()

    assert [(sample_id, pdf, digest, json.loads(reading)) for sample_id, pdf, digest, reading in seeded()] == expected()


async def test_sample_run_reads_the_table_not_the_disk(client: httpx.AsyncClient, no_disk: None) -> None:
    run_id = (await client.post("/v1/runs/samples/INV-001")).json()["id"]
    events = await stream(client, run_id)
    detail = (await client.get(f"/v1/runs/{run_id}")).json()

    assert events[-1][1]["outcome"] == "approved"
    assert (detail["mode"], detail["vendor_id"], detail["po"], detail["number"]) == (
        "recorded",
        "V001",
        "PO-2024-0142",
        "ACM/24-25/0187",
    )


async def test_identical_bytes_replay_under_any_name(client: httpx.AsyncClient, no_disk: None) -> None:
    pdf = expected()[1][1]

    created = await client.post("/v1/runs", files={"file": ("scan from vendor.pdf", pdf, "application/pdf")})
    await stream(client, created.json()["id"])
    detail = (await client.get(f"/v1/runs/{created.json()['id']}")).json()

    assert (detail["file"], detail["outcome"], detail["vendor_id"], detail["po"]) == (
        "scan from vendor.pdf",
        "approved",
        "V002",
        "PO-2024-0143",
    )
    assert detail["tokens"] == {"in": 0, "read": 0, "write": 0, "out": 0}


async def test_listing_samples_never_selects_the_heavy_columns(
    client: httpx.AsyncClient, statements: list[str]
) -> None:
    samples = (await client.get("/v1/admin/samples")).json()["data"]
    reads = [sql for sql in statements if "FROM samples" in sql]

    assert len(samples) == 46
    assert reads
    assert not [sql for sql in reads if "samples.pdf" in sql or "samples.reading" in sql]


async def test_any_sample_runs_at_any_time(client: httpx.AsyncClient) -> None:
    unknown = await client.post("/v1/runs/samples/INV-099")
    dependent = await client.post("/v1/runs/samples/INV-024")
    await stream(client, dependent.json()["id"])
    detail = (await client.get(f"/v1/runs/{dependent.json()['id']}")).json()
    orders = {order["po"]: order for order in (await client.get("/v1/orders")).json()["data"]}

    assert (unknown.status_code, unknown.json()) == (404, {"detail": "Unknown sample INV-099"})
    assert dependent.status_code == 202
    assert (detail["outcome"], detail["po"]) == ("approved", "PO-2024-0142")
    assert orders["PO-2024-0142"]["left"] == "36000.00"


async def test_sample_route_reads_one_sample_and_no_run(
    client: httpx.AsyncClient, statements: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def submitted(*_: object) -> str:
        return "0" * 32

    monkeypatch.setattr(EntitiesRegistry.Pipeline, "submit", submitted)
    statements.clear()

    response = await client.post("/v1/runs/samples/INV-024")
    samples = [sql for sql in statements if "FROM samples" in sql]

    assert (response.status_code, response.json()) == (202, {"id": "0" * 32})
    assert len(samples) == 1
    assert "samples.pdf" in samples[0]
    assert "WHERE samples.id = ?" in samples[0]
    assert "JOIN" not in samples[0]
    assert not [sql for sql in statements if "FROM runs" in sql or "reading" in sql]
