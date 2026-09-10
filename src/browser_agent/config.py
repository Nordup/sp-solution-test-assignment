"""Local settings; credentials are loaded only when explicitly requested."""

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
    max_input_tokens: int = Field(default=20000, ge=1000, le=20000)
    max_output_tokens: int = Field(default=2048, ge=128, le=2048)
    max_decisions: int = Field(default=60, ge=1, le=120)
    active_seconds: int = Field(default=1200, ge=1, le=1200)
    max_retries: int = Field(default=2, ge=0, le=3)
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
        for directory in ("runs", "profiles"):
            (self.artifact_dir / directory).mkdir(mode=0o700, exist_ok=True)
