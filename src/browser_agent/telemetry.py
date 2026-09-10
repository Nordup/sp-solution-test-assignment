"""Private local event sink for task runs."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console


class Events:
    def __init__(self, path: Path, console: Console | None = None):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = path.open("a", encoding="utf-8")
        path.chmod(0o600)
        self.console = console
        self.secrets = [
            v
            for k, v in os.environ.items()
            if ("KEY" in k or "TOKEN" in k or "SECRET" in k) and len(v) > 12
        ]

    def __call__(self, event, data):
        payload = json.dumps(
            {"time": datetime.now(UTC).isoformat(), "event": event, **data},
            ensure_ascii=False,
            default=str,
        )
        for secret in self.secrets:
            payload = payload.replace(secret, "[REDACTED]")
        self.file.write(payload + "\n")
        self.file.flush()
        if self.console:
            record = json.loads(payload)
            if event == "tool_proposed":
                label = f"Tool {record['step']}: {record['tool']}"
                message = json.dumps(record["arguments"], ensure_ascii=False)
            elif event == "observe":
                label = "Page"
                message = f"{record.get('title', '')}\n{record.get('url', '')}"
            elif event == "tool_result":
                label = "Result"
                result = record["result"]
                message = (
                    result.get("status", "")
                    + "\n"
                    + result.get("observed_excerpt", "")[:700]
                )
            elif event == "security_review":
                label = "Security"
                message = (
                    f"{record.get('decision', 'unknown')}: "
                    f"{record.get('reason', '')[:500]}"
                )
            elif event in {"recovery", "provider_retry", "run_error", "run_cancelled"}:
                label = event
                message = json.dumps(data, ensure_ascii=False)
                for secret in self.secrets:
                    message = message.replace(secret, "[REDACTED]")
            else:
                # Approval details and the final report are displayed by the CLI.
                return
            self.console.print(
                label + " ",
                style="cyan",
                end="",
            )
            self.console.print(
                message,
                markup=False,
            )

    def close(self):
        self.file.close()
