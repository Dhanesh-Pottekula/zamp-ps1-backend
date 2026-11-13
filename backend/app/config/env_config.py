"""
Env-backed configuration for the invoice pipeline.

Groups:
- Llm: extraction provider and model, effort, cache lifetime and the recorded / live switch
- Rules: tolerance policy, matching weights with their pass score, and intake limits
- Storage: database, upload and seed data locations (relative paths resolve against the backend folder)
"""

from pathlib import Path
from typing import Final, Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class _Helper:
    """Hold the folder every relative setting resolves against."""

    BACKEND_DIR: Final = Path(__file__).resolve().parents[2]
    CONFIG_DIR: Final = Path(__file__).resolve().parent


class _LlmConfigClass(BaseSettings):
    """Load extraction model settings from llm.env and the secret keys from backend/.env."""

    mode: Literal["auto", "live", "recorded"] = "auto"
    provider: Literal["auto", "anthropic", "openai"] = "auto"
    model: str = "claude-opus-5-5"
    openai_model: str = "gpt-6-luna"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    cache_ttl: Literal["5m", "1h"] = "5m"
    max_tokens: int = Field(default=16000, gt=0, le=20000)
    replay_delay: float = 0.8
    api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")

    model_config = SettingsConfigDict(
        env_file=(_Helper.CONFIG_DIR / "llm.env", _Helper.BACKEND_DIR / ".env"),
        env_prefix="LLM_",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def chosen(self) -> Literal["anthropic", "openai"]:
        """Return the resolved provider: forced value, else Anthropic unless only the OpenAI key is set."""
        if self.provider != "auto":
            return self.provider

        return "openai" if self._secret(self.openai_key) and not self._secret(self.api_key) else "anthropic"

    @property
    def chosen_key(self) -> str:
        """Return the chosen provider's key, or an empty string when it is not set."""
        return self._secret(self.openai_key if self.chosen == "openai" else self.api_key)

    @property
    def chosen_model(self) -> str:
        """Return the model of the chosen provider."""
        return self.openai_model if self.chosen == "openai" else self.model

    @property
    def active(self) -> Literal["live", "recorded"]:
        """Return the resolved mode: forced value, else live only when the chosen provider's key is set."""
        if self.mode != "auto":
            return self.mode

        return "live" if self.chosen_key else "recorded"

    @staticmethod
    def _secret(key: SecretStr | None) -> str:
        """Return a key's value without surrounding blanks, or an empty string."""
        return key.get_secret_value().strip() if key else ""


class _RulesConfigClass(BaseSettings):
    """Load tolerance, matching weights and intake limits from rules.env."""

    tolerance_pct: float = 2.0
    tolerance_floors: dict[str, float] = {"INR": 500, "USD": 10, "EUR": 10, "GBP": 10, "JPY": 1000}
    pass_score: float = Field(default=80.0, ge=0, lt=100)
    vendor_weights: dict[Literal["tax_id", "bank_account", "name"], float] = {
        "tax_id": 80,
        "bank_account": 60,
        "name": 30,
    }
    line_sure_above: float = Field(default=90.0, ge=0, le=100)
    line_candidate_at: float = Field(default=70.0, ge=0, le=100)
    line_coverage_at: float = Field(default=80.0, ge=0, le=100)
    po_pool_max: int = Field(default=10, gt=0)
    max_pages: int = 10
    max_bytes: int = 20971520

    model_config = SettingsConfigDict(
        env_file=_Helper.CONFIG_DIR / "rules.env", env_prefix="RULES_", env_file_encoding="utf-8", extra="ignore"
    )

    @field_validator("vendor_weights")
    @classmethod
    def every_signal(cls, value: dict[str, float], info: ValidationInfo) -> dict[str, float]:
        """Refuse a weight table that leaves a signal out or gives one a negative weight."""
        default = cls.model_fields[info.field_name or ""].default
        if set(value) != set(default) or min(value.values()) < 0:
            message = f"{info.field_name} needs a non-negative weight for each of: {', '.join(default)}"
            raise ValueError(message)

        return value


class _StorageConfigClass(BaseSettings):
    """Load storage locations from storage.env."""

    db_path: Path = Path("var/ps1.sqlite3")
    upload_dir: Path = Path("var/uploads")
    data_dir: Path = Path("data")
    cors_origin: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file=_Helper.CONFIG_DIR / "storage.env", env_prefix="STORAGE_", env_file_encoding="utf-8", extra="ignore"
    )

    @field_validator("db_path", "upload_dir", "data_dir")
    @classmethod
    def anchor_path(cls, value: Path) -> Path:
        """Resolve a relative path against the backend folder so the working directory never matters."""
        return value if value.is_absolute() else _Helper.BACKEND_DIR / value


class _JevConfigClass(BaseSettings):
    """Load the Jev PO-selection settings from jev.env and the TypeSafe key from backend/.env."""

    mode: Literal["auto", "live", "recorded"] = "auto"
    model: str = "jev-1.13.0"
    timeout: float = Field(default=10.0, gt=0)
    select_above: float = Field(default=0.80, gt=0, lt=1)
    reject_at_or_below: float = Field(default=0.50, ge=0, lt=1)
    max_candidates: int = Field(default=10, gt=0)
    record: bool = False
    recordings: Path = Path("data/jev_recordings.json")
    api_key: SecretStr | None = Field(default=None, alias="TYPESAFE_API_KEY")

    model_config = SettingsConfigDict(
        env_file=(_Helper.CONFIG_DIR / "jev.env", _Helper.BACKEND_DIR / ".env"),
        env_prefix="JEV_",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("recordings")
    @classmethod
    def anchor_path(cls, value: Path) -> Path:
        """Resolve a relative path against the backend folder so the working directory never matters."""
        return value if value.is_absolute() else _Helper.BACKEND_DIR / value

    @field_validator("reject_at_or_below")
    @classmethod
    def below_select(cls, value: float, info: ValidationInfo) -> float:
        """Refuse a rejection bound at or above the selection bound."""
        if value >= info.data.get("select_above", 1.0):
            message = "reject_at_or_below must be lower than select_above"
            raise ValueError(message)

        return value

    @property
    def key(self) -> str:
        """Return the TypeSafe key without surrounding blanks, or an empty string."""
        return self.api_key.get_secret_value().strip() if self.api_key else ""

    @property
    def active(self) -> Literal["live", "recorded"]:
        """Return the resolved mode: forced value, else live only when the TypeSafe key is set."""
        if self.mode != "auto":
            return self.mode

        return "live" if self.key else "recorded"


class ENVConfigsClass:
    """Expose every settings group as one object."""

    def __init__(self) -> None:
        """Load each settings group once."""
        self.Jev: Final = _JevConfigClass()
        self.Llm: Final = _LlmConfigClass()
        self.Rules: Final = _RulesConfigClass()
        self.Storage: Final = _StorageConfigClass()
