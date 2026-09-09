"""Validated local configuration. Importing this module never reads credentials."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "gpt-5.6-luna"
    api_key: SecretStr = SecretStr("")
    artifact_dir: Path = Path("artifacts")
    budget_usd: float = Field(default=5, gt=0, le=5)
    max_input_tokens: int = Field(default=20_000, ge=1000, le=20_000)
    max_output_tokens: int = Field(default=2048, ge=128, le=2048)
    max_decisions: int = Field(default=60, ge=1, le=120)
    active_seconds: int = Field(default=1200, ge=1, le=1200)
    reasoning: str = "low"

    @classmethod
    def load(cls, **overrides):
        load_dotenv(".env.local", override=False)
        return cls(
            **{
                "model": os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
                "api_key": os.getenv("OPENAI_API_KEY", ""),
                "artifact_dir": Path(os.getenv("AGENT_ARTIFACT_DIR", "artifacts")),
                "budget_usd": float(os.getenv("AGENT_BUDGET_USD", "5")),
                **overrides,
            }
        )

    def prepare(self):
        self.artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.artifact_dir.chmod(0o700)
        for subdir in ["state", "runs", "profiles", "evals", "final"]:
            (self.artifact_dir / subdir).mkdir(mode=0o700, exist_ok=True)
