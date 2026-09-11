"""Local settings; credentials are loaded only when explicitly requested."""

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    """Runtime limits and browser options, with opt-in environment loading."""

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

    @classmethod
    def load(cls, **overrides: Any) -> "Settings":
        """Load local credentials without overriding the process environment."""
        load_dotenv(".env.local", override=False)
        profile = os.getenv("AGENT_BROWSER_PROFILE_DIR")
        values = {
            "model": os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
            "api_key": os.getenv("OPENAI_API_KEY", ""),
            "artifact_dir": Path(os.getenv("AGENT_ARTIFACT_DIR", "artifacts")),
            "budget_usd": float(os.getenv("AGENT_BUDGET_USD", "5")),
            "playwright_cli_command": os.getenv("PLAYWRIGHT_CLI_COMMAND", "npx"),
            "playwright_cli_package": os.getenv(
                "PLAYWRIGHT_CLI_PACKAGE", "@playwright/cli@0.1.19"
            ),
            "browser_headed": os.getenv("AGENT_BROWSER_HEADED", "1").strip().casefold()
            not in {"0", "false", "no", "off"},
            "browser_channel": os.getenv("AGENT_BROWSER_CHANNEL") or None,
            "browser_cdp_endpoint": os.getenv("AGENT_BROWSER_CDP_ENDPOINT") or None,
            "browser_profile_dir": Path(profile) if profile else None,
        }
        return cls(**(values | overrides))

    def prepare(self) -> None:
        """Create the private directories used by runs and browser sessions."""
        self.artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for directory in ("runs", "profiles", "browser"):
            (self.artifact_dir / directory).mkdir(mode=0o700, exist_ok=True)
