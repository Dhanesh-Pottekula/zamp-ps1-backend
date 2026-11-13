"""Transport models for runs: creation, a typed invoice, history, detail and the live event stream."""

from datetime import datetime  # noqa: TC003 - pydantic resolves field types at runtime
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field

from app.entities import FindingTypes, StageTypes  # noqa: TC001 - pydantic resolves field types at runtime
from app.routers.config import RouterBaseModel, RouterTypes


class _Types:
    """Parts of the run requests and responses."""

    type Typed = Annotated[str, Field(max_length=500)]

    class Strict(RouterBaseModel):
        """Base for a typed invoice: unknown keys are refused."""

        model_config = ConfigDict(extra="forbid")

    class TypedLine(Strict):
        """One invoice line as typed."""

        description: _Types.Typed = ""
        quantity: _Types.Typed = ""
        unit: _Types.Typed = ""
        unit_price: _Types.Typed = ""
        line_amount: _Types.Typed = ""
        tax_rate_pct: _Types.Typed = ""

    class TypedTax(Strict):
        """One tax line as typed."""

        label: _Types.Typed = ""
        rate_pct: _Types.Typed = ""
        amount: _Types.Typed = ""

    class Tokens(RouterBaseModel):
        """Token usage of the extraction call."""

        input: int = Field(serialization_alias="in")
        cache_read: int = Field(serialization_alias="read")
        cache_write: int = Field(serialization_alias="write")
        output: int = Field(serialization_alias="out")

    class Finding(RouterBaseModel):
        """One finding."""

        code: FindingTypes.FindingCode
        severity: FindingTypes.Severity = Field(serialization_alias="level")
        message: str = Field(serialization_alias="text")
        data: dict[str, str]

    class Step(RouterBaseModel):
        """One stage of a run; data is the stage result without its findings."""

        name: StageTypes.StepName
        state: StageTypes.StepState
        millis: int = Field(serialization_alias="ms")
        data: dict[str, Any]
        findings: list[_Types.Finding]


class RunModels:
    """Versioned run transport contracts."""

    class V1:
        """Version 1 run contracts."""

        Step = _Types.Step

        class Created(RouterBaseModel):
            """The id of a run that was accepted for processing."""

            id: str

        class Manual(_Types.Strict):
            """An invoice typed by a person: the fields the model fills, as text, without source quotes."""

            supplier_name: _Types.Typed = ""
            supplier_tax_id: _Types.Typed = ""
            supplier_address: _Types.Typed = ""
            supplier_email: _Types.Typed = ""
            bank_account_name: _Types.Typed = ""
            bank_account_number: _Types.Typed = ""
            bank_code: _Types.Typed = ""
            bank_name: _Types.Typed = ""
            buyer_name: _Types.Typed = ""
            buyer_tax_id: _Types.Typed = ""
            invoice_number: _Types.Typed = ""
            invoice_date: _Types.Typed = ""
            due_date: _Types.Typed = ""
            currency: _Types.Typed = ""
            date_order: Literal["DMY", "MDY", "YMD", ""] = ""
            decimal_separator: Literal[".", ",", ""] = "."
            po_reference_text: _Types.Typed = ""
            prices_include_tax: bool = False
            lines: list[_Types.TypedLine] = Field(default=[], max_length=50)
            subtotal: _Types.Typed = ""
            tax_lines: list[_Types.TypedTax] = Field(default=[], max_length=10)
            total: _Types.Typed = ""

        class Run(RouterBaseModel):
            """One run as listed in history."""

            id: str
            filename: str | None = Field(serialization_alias="file")
            sample: str | None
            status: StageTypes.RunStatus
            outcome: StageTypes.Outcome | None
            vendor_id: str | None
            vendor_name: str | None = Field(serialization_alias="vendor")
            po_number: str | None = Field(serialization_alias="po")
            invoice_id: int | None
            invoice_number: str | None = Field(serialization_alias="number")
            amount: RouterTypes.Money | None
            currency: str | None
            summary: str | None
            tokens: _Types.Tokens | None
            mode: StageTypes.RunMode
            created_at: datetime = Field(serialization_alias="at")
            millis: int | None = Field(serialization_alias="ms")

        class Detail(Run):
            """One run with every finding of its steps in reason order, and its steps."""

            reasons: list[_Types.Finding]
            steps: list[_Types.Step]

        class Runs(RouterBaseModel):
            """Run history."""

            data: list[RunModels.V1.Run]

        class Done(RouterBaseModel):
            """The closing event of a run's stream."""

            status: StageTypes.RunStatus
            outcome: StageTypes.Outcome | None
            summary: str | None
