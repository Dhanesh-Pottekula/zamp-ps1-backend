"""
Seed data: owns the sample scenario table and restores vendors, POs and samples from the seed files.

The files under data/ are only the seed: samples.json with each sample's PDF and recorded reading fills the samples
table, the CSVs fill vendors and POs. Nothing reads them after seeding; a recording is found by the PDF's SHA-256.
"""

import asyncio
import csv
import hashlib
import io
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import JSON, Integer, LargeBinary, String, delete, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters import Base
from app.entities._types import ExtractionTypes, StageTypes

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.adapters import AdapterTypes
    from app.entities.invoice_ent import InvoiceEntityClass
    from app.entities.order_ent import OrderEntityClass
    from app.entities.run_ent import RunEntityClass
    from app.entities.vendor_ent import VendorEntityClass


class SeedDB:
    """Sample scenario table."""

    class Samples(Base):
        """One step of the ordered demo scenario."""

        __tablename__ = "samples"

        id: Mapped[str] = mapped_column(String(16), primary_key=True)
        step: Mapped[int] = mapped_column(Integer, unique=True)
        title: Mapped[str] = mapped_column(String(200))
        expect: Mapped[str] = mapped_column(String(1000))
        outcome: Mapped[str] = mapped_column(String(20))
        pdf: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
        digest: Mapped[str] = mapped_column(String(64), index=True)
        reading: Mapped[dict[str, Any]] = mapped_column(JSON, deferred=True)


class _Types:
    """The sample scenario row and the PDF that running one sample takes."""

    class Sample(BaseModel):
        """One step of the ordered demo scenario."""

        step: int
        id: str
        title: str
        expect: str
        outcome: StageTypes.Outcome

    class SampleFetch(BaseModel):
        """One sample's PDF."""

        id: str
        pdf: bytes


class _Helper:
    """Parse seed files, fingerprint documents and rewrite the samples table."""

    SAMPLES: Final = TypeAdapter(list[_Types.Sample])

    @staticmethod
    def rows(data: bytes) -> list[dict[str, str]]:
        """Parse CSV bytes into rows keyed by header."""
        return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))

    @staticmethod
    def digest(document: bytes) -> str:
        """Return the SHA-256 hex digest that identifies a document by its content."""
        return hashlib.sha256(document).hexdigest()

    @staticmethod
    def samples_in(session: Session, rows: list[SeedDB.Samples]) -> None:
        """Replace every sample with the seed scenario rows inside the caller's transaction."""
        session.execute(delete(SeedDB.Samples))
        session.add_all(rows)


class SeedEntityClass:
    """Serve the sample scenario and restore the demo data."""

    def __init__(
        self,
        db_adapter: AdapterTypes.DB,
        store_adapter: AdapterTypes.Store,
        *,
        vendor_entity: VendorEntityClass,
        order_entity: OrderEntityClass,
        invoice_entity: InvoiceEntityClass,
        run_entity: RunEntityClass,
    ) -> None:
        """Keep the adapters and the entities whose tables a reset rewrites."""
        self._db_adapter = db_adapter
        self._store_adapter = store_adapter
        self._vendor_entity = vendor_entity
        self._order_entity = order_entity
        self._invoice_entity = invoice_entity
        self._run_entity = run_entity

    async def initialize(self) -> None:
        """Seed every table when the vendor master is empty; otherwise seed only an empty samples table."""
        if not await self._vendor_entity.seeded():
            await self.reset()
            return

        count = select(func.count()).select_from(SeedDB.Samples)
        if not await self._db_adapter.run(lambda session: session.scalar(count)):
            rows = await self._scenario(await self._store_adapter.read_data("samples.json"))
            await self._db_adapter.run(lambda session: _Helper.samples_in(session, rows))

    async def reset(self) -> None:
        """Delete runs, steps, the register and uploads, and restore samples, vendors and POs to the seed files."""
        *tables, scenario = await asyncio.gather(
            *(
                self._store_adapter.read_data(name)
                for name in ("vendors.csv", "purchase_orders.csv", "po_lines.csv", "samples.json")
            )
        )
        vendors, orders, lines = (_Helper.rows(data) for data in tables)
        samples = await self._scenario(scenario)

        def work(session: Session) -> None:
            self._run_entity.clear_in(session)
            self._invoice_entity.clear_in(session)
            _Helper.samples_in(session, samples)
            names = self._vendor_entity.reset_in(session, vendors)
            self._order_entity.reset_in(session, orders, lines, names)

        await self._db_adapter.run(work)
        await self._store_adapter.clear_uploads()
        self._run_entity.end_streams("The demo data was reset; this run no longer exists.")

    async def samples(self) -> list[_Types.Sample]:
        """Return the scenario ordered by step."""
        query = select(SeedDB.Samples).order_by(SeedDB.Samples.step)
        return await self._db_adapter.run(
            lambda session: [_Types.Sample.model_validate(row, from_attributes=True) for row in session.scalars(query)]
        )

    async def sample(self, sample_id: str) -> _Types.SampleFetch | None:
        """Return one sample's PDF, or None when the sample is unknown."""
        query = select(SeedDB.Samples.id, SeedDB.Samples.pdf).where(SeedDB.Samples.id == sample_id)

        def load(session: Session) -> _Types.SampleFetch | None:
            row = session.execute(query).one_or_none()
            return None if row is None else _Types.SampleFetch(id=row.id, pdf=row.pdf)

        return await self._db_adapter.run(load)

    async def recording(self, document: bytes) -> ExtractionTypes.InvoiceRead | None:
        """Return the recorded reading of the sample whose PDF has exactly these bytes, or None."""
        query = select(SeedDB.Samples.reading).where(SeedDB.Samples.digest == _Helper.digest(document)).limit(1)
        reading = await self._db_adapter.run(lambda session: session.scalar(query))
        return None if reading is None else ExtractionTypes.InvoiceRead.model_validate(reading)

    async def _scenario(self, scenario: bytes) -> list[SeedDB.Samples]:
        """
        Build the samples table rows from samples.json and each sample's PDF and recorded reading on disk.

        Raises:
            pydantic.ValidationError: a recorded reading does not match InvoiceRead.

        """
        samples = _Helper.SAMPLES.validate_json(scenario)
        files = await asyncio.gather(
            *(
                self._store_adapter.read_data(f"invoices/{sample.id}{suffix}")
                for sample in samples
                for suffix in (".pdf", ".read.json")
            )
        )
        return [
            SeedDB.Samples(
                **sample.model_dump(),
                pdf=pdf,
                digest=_Helper.digest(pdf),
                reading=ExtractionTypes.InvoiceRead.model_validate_json(reading).model_dump(mode="json"),
            )
            for sample, pdf, reading in zip(samples, files[::2], files[1::2], strict=True)
        ]
