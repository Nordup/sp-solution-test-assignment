"""Private local event sink. External tracing is explicitly enabled only by fixtures."""

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
            self.console.print(
                f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {event} ",
                style="cyan",
                end="",
            )
            self.console.print(
                json.dumps(json.loads(payload), ensure_ascii=False)[0:1500],
                markup=False,
            )

    def close(self):
        self.file.close()
