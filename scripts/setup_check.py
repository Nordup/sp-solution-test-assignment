"""Offline credential/dependency checks. Never prints secret values or spends tokens."""

import importlib.metadata
import json
import stat
import subprocess
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
env_path = root / ".env.local"
env = dotenv_values(env_path)
required = (
    "OPENAI_API_KEY",
    "OPENAI_PROJECT_ID",
    "LANGSMITH_API_KEY",
    "LANGSMITH_WORKSPACE_ID",
    "LANGSMITH_PROJECT",
)
ignored = (
    subprocess.run(
        ["git", "check-ignore", "-q", ".env.local"], cwd=root, check=False
    ).returncode
    == 0
)
tracked = bool(
    subprocess.check_output(["git", "ls-files", ".env.local"], cwd=root).strip()
)
private = env_path.exists() and stat.S_IMODE(env_path.stat().st_mode) == 0o600
report = {
    "credentials_present": {k: bool(env.get(k)) for k in required},
    "env_gitignored": ignored,
    "env_tracked": tracked,
    "env_owner_only": private,
    "model": env.get("OPENAI_MODEL"),
    "project": env.get("LANGSMITH_PROJECT"),
    "dependencies": {
        p: importlib.metadata.version(p)
        for p in ("openai", "langgraph", "langsmith", "playwright", "pydantic")
    },
}
print(json.dumps(report, indent=2))
raise SystemExit(
    0
    if all(report["credentials_present"].values())
    and ignored
    and not tracked
    and private
    else 1
)
