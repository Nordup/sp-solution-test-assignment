"""Validated runtime settings loaded from the process environment on demand."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr

_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})
_PRIVATE_DIRECTORY_MODE = 0o700


def _environment_flag(value: str) -> bool:
    """Match the CLI's established permissive boolean environment syntax."""

    return value.strip().casefold() not in _DISABLED_VALUES


class Settings(BaseModel):
    """Runtime limits, credentials, and browser process configuration."""

    model_config = ConfigDict(extra="forbid")

    model: str = "gpt-5.6-luna"
    api_key: SecretStr = SecretStr("")
    artifact_dir: Path = Path("artifacts")
    budget_usd: float = Field(default=5, gt=0, le=5)
    max_input_tokens: int = Field(default=200_000, ge=1_000, le=200_000)
    max_output_tokens: int = Field(default=32_768, ge=128, le=128_000)
    compact_threshold: int = Field(default=150_000, ge=1_000, le=200_000)
    active_seconds: int = Field(default=1_200, ge=1, le=1_200)
    max_retries: int = Field(default=2, ge=0, le=3)
    reasoning: str = "max"

    playwright_cli_command: str = "npx"
    playwright_cli_package: str = "@playwright/cli@0.1.19"
    browser_headed: bool = True
    browser_channel: str | None = None
    browser_cdp_endpoint: str | None = None
    browser_profile_dir: Path | None = None

    _ENVIRONMENT_FIELDS: ClassVar[dict[str, str]] = {
        "model": "OPENAI_MODEL",
        "api_key": "OPENAI_API_KEY",
        "artifact_dir": "AGENT_ARTIFACT_DIR",
        "budget_usd": "AGENT_BUDGET_USD",
        "playwright_cli_command": "PLAYWRIGHT_CLI_COMMAND",
        "playwright_cli_package": "PLAYWRIGHT_CLI_PACKAGE",
        "browser_headed": "AGENT_BROWSER_HEADED",
        "browser_channel": "AGENT_BROWSER_CHANNEL",
        "browser_cdp_endpoint": "AGENT_BROWSER_CDP_ENDPOINT",
        "browser_profile_dir": "AGENT_BROWSER_PROFILE_DIR",
    }
    _OPTIONAL_ENVIRONMENT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"browser_channel", "browser_cdp_endpoint", "browser_profile_dir"}
    )

    @classmethod
    def load(cls, **overrides: Any) -> Settings:
        """Load ``.env.local`` without replacing values already in the process."""

        load_dotenv(dotenv_path=Path(".env.local"), override=False)
        values: dict[str, Any] = {}
        for field, variable in cls._ENVIRONMENT_FIELDS.items():
            value = os.getenv(variable)
            if value is None:
                continue
            if field == "browser_headed":
                values[field] = _environment_flag(value)
            elif field in cls._OPTIONAL_ENVIRONMENT_FIELDS:
                values[field] = value or None
            else:
                values[field] = value
        values.update(overrides)
        return cls.model_validate(values)

    def prepare(self) -> None:
        """Create each local artifact directory with private permissions."""

        directories = (
            self.artifact_dir,
            self.artifact_dir / "runs",
            self.artifact_dir / "profiles",
            self.artifact_dir / "browser",
        )
        for directory in directories:
            directory.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
            directory.chmod(_PRIVATE_DIRECTORY_MODE)
