"""
Entity package: builds the shared registry and exposes entity-owned types to services.

Two kinds of objects live here. Entities own data: the AP policy (Rules), prompts, the vendor master, purchase
orders, the invoice register, runs and seed data. Stages (stages/) own the logic of one check each and read and
write data only through the entities they are given. The pipeline runs the stages in order.

Startup order: construct every entity and stage, create missing tables, then load prompts, close interrupted runs
and seed data (vendors, POs and the samples table) together.
"""

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING

from app.config import ENVConfigs
from app.entities._types import ExtractionTypes, FindingTypes, MasterTypes, StageTypes
from app.entities.invoice_ent import InvoiceEntityClass, InvoiceTypes
from app.entities.order_ent import OrderEntityClass, OrderTypes
from app.entities.pipeline_invoice import PipelineEntityClass
from app.entities.prompt_ent import PromptEntityClass
from app.entities.rules_ent import RulesEntityClass
from app.entities.run_ent import RunEntityClass, RunTypes
from app.entities.seed_ent import SeedEntityClass
from app.entities.stages.amounts_stage import AmountsStageClass
from app.entities.stages.bank_stage import BankStageClass
from app.entities.stages.decision_stage import DecisionStageClass
from app.entities.stages.duplicate_stage import DuplicateStageClass
from app.entities.stages.extraction_stage import ExtractionStageClass
from app.entities.stages.intake_stage import IntakeStageClass
from app.entities.stages.persist_stage import PersistStageClass
from app.entities.stages.po_jev import PoJevClass
from app.entities.stages.po_stage import PoStageClass
from app.entities.stages.validation_stage import ValidationStageClass
from app.entities.stages.vendor_stage import VendorStageClass
from app.entities.vendor_ent import VendorEntityClass, VendorTypes

if TYPE_CHECKING:
    from app.adapters import AdapterRegistry


class EntitiesRegistry:
    """Hold the shared entities and stages for the application lifetime."""

    # Data and policy
    Prompt: PromptEntityClass
    Rules: RulesEntityClass
    Vendor: VendorEntityClass
    Order: OrderEntityClass
    Invoice: InvoiceEntityClass
    Run: RunEntityClass
    Seed: SeedEntityClass

    # The ten stages, in run order (Jev is part of the PO stage)
    IntakeStage: IntakeStageClass
    ExtractionStage: ExtractionStageClass
    ValidationStage: ValidationStageClass
    VendorStage: VendorStageClass
    BankStage: BankStageClass
    DuplicateStage: DuplicateStageClass
    Jev: PoJevClass
    PoStage: PoStageClass
    AmountsStage: AmountsStageClass
    DecisionStage: DecisionStageClass
    PersistStage: PersistStageClass

    Pipeline: PipelineEntityClass

    @classmethod
    async def initialize(cls, adapter: AdapterRegistry) -> None:
        """Construct every entity and stage from the adapters and settings, create tables, then seed."""
        jev, llm, rules = ENVConfigs.Jev, ENVConfigs.Llm, ENVConfigs.Rules

        cls.Prompt = PromptEntityClass()
        floors = {currency: Decimal(str(floor)) for currency, floor in rules.tolerance_floors.items()}
        cls.Rules = RulesEntityClass(
            Decimal(str(rules.tolerance_pct)),
            floors,
            line_sure_above=rules.line_sure_above,
            line_candidate_at=rules.line_candidate_at,
            line_coverage_at=rules.line_coverage_at,
        )
        cls.Vendor = VendorEntityClass(adapter.DB, rules_entity=cls.Rules)
        cls.Order = OrderEntityClass(adapter.DB, adapter.Match, vendor_entity=cls.Vendor)
        cls.Invoice = InvoiceEntityClass(adapter.DB)
        cls.Run = RunEntityClass(adapter.DB)
        cls.Seed = SeedEntityClass(
            adapter.DB,
            adapter.Store,
            vendor_entity=cls.Vendor,
            order_entity=cls.Order,
            invoice_entity=cls.Invoice,
            run_entity=cls.Run,
        )

        cls.IntakeStage = IntakeStageClass(
            adapter.Pdf, adapter.Store, max_bytes=rules.max_bytes, max_pages=rules.max_pages
        )
        cls.ExtractionStage = ExtractionStageClass(
            adapter.LLM, cls.Prompt, llm.chosen_model, seed_entity=cls.Seed, replay_delay=llm.replay_delay
        )
        cls.ValidationStage = ValidationStageClass(cls.Rules)
        cls.VendorStage = VendorStageClass(
            cls.Vendor,
            adapter.Match,
            rules_entity=cls.Rules,
            weights=rules.vendor_weights,
            pass_score=rules.pass_score,
        )
        cls.BankStage = BankStageClass(cls.Vendor)
        cls.DuplicateStage = DuplicateStageClass(cls.Invoice)
        cls.Jev = PoJevClass(
            adapter.Jev,
            recordings=jev.recordings,
            record=jev.record,
            select_above=jev.select_above,
            reject_at_or_below=jev.reject_at_or_below,
            timeout=jev.timeout,
        )
        cls.PoStage = PoStageClass(
            cls.Order, cls.Jev, rules_entity=cls.Rules, pool_max=rules.po_pool_max, pass_score=rules.pass_score
        )
        cls.AmountsStage = AmountsStageClass(cls.Rules, cls.Order)
        cls.DecisionStage = DecisionStageClass(cls.Rules)
        cls.PersistStage = PersistStageClass(
            adapter.DB, run_entity=cls.Run, invoice_entity=cls.Invoice, order_entity=cls.Order
        )

        cls.Pipeline = PipelineEntityClass(
            adapter.Store,
            intake=cls.IntakeStage,
            extraction=cls.ExtractionStage,
            validation=cls.ValidationStage,
            vendor=cls.VendorStage,
            bank=cls.BankStage,
            duplicate=cls.DuplicateStage,
            po=cls.PoStage,
            amounts=cls.AmountsStage,
            decision=cls.DecisionStage,
            persist=cls.PersistStage,
            rules_entity=cls.Rules,
            vendor_entity=cls.Vendor,
            run_entity=cls.Run,
            seed_entity=cls.Seed,
        )

        await adapter.DB.create_tables()
        await asyncio.gather(cls.Prompt.initialize(), cls.Run.initialize(), cls.Seed.initialize())


__all__ = [
    "EntitiesRegistry",
    "ExtractionTypes",
    "FindingTypes",
    "InvoiceTypes",
    "MasterTypes",
    "OrderTypes",
    "RunTypes",
    "StageTypes",
    "VendorTypes",
]
