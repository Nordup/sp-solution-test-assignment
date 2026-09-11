"""Fresh browser evidence retained for the independent action reviewer."""

from __future__ import annotations

import json
from typing import Any

from .artifacts import ArtifactStore

MAX_EVIDENCE_CHARS = 1_000_000


class EvidenceCache:
    """Retain current page evidence without confusing it with tool output."""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts
        self._evidence = ""
        self._page_evidence = ""
        self._current_url: str | None = None

    @property
    def text(self) -> str:
        return self._evidence

    @property
    def current_url(self) -> str | None:
        return self._current_url

    def clear(self) -> None:
        self._evidence = ""
        self._page_evidence = ""

    def update(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        fresh_after_ns: int | None = None,
    ) -> None:
        """Update cached evidence from one successful CLI response."""

        snapshot = self._artifacts.snapshot_text(
            command,
            payload,
            fresh_after_ns=fresh_after_ns,
            limit=MAX_EVIDENCE_CHARS,
        )
        result = payload.get("result")
        if result is None and any(
            key in payload for key in ("snapshot", "url", "title")
        ):
            result = {
                key: payload[key]
                for key in ("snapshot", "url", "title")
                if key in payload
            }

        output = result if isinstance(result, str) else ""
        if snapshot:
            output = snapshot
        elif isinstance(result, (dict, list)):
            output = json.dumps(result, ensure_ascii=False)

        snapshot_value = payload.get("snapshot")
        if snapshot_value is None and isinstance(result, dict):
            snapshot_value = result.get("snapshot")
        inline_snapshot = snapshot_value is not None and not (
            isinstance(snapshot_value, dict)
            and set(snapshot_value).issubset({"file", "path"})
        )
        if command == "snapshot" and output and snapshot_value is None:
            inline_snapshot = True

        if snapshot or inline_snapshot:
            self._page_evidence = output[:MAX_EVIDENCE_CHARS]
        elif command == "find" and output:
            find_excerpt = f"Find result: {output}"
            if self._page_evidence:
                self._page_evidence = (self._page_evidence + "\n" + find_excerpt)[
                    :MAX_EVIDENCE_CHARS
                ]
            else:
                self._page_evidence = find_excerpt[:MAX_EVIDENCE_CHARS]

        details: list[str] = []
        self._append_metadata(details, payload)
        if isinstance(result, dict):
            self._append_metadata(details, result)
        if self._page_evidence:
            details.append(f"Page evidence: {self._page_evidence}")
        if output and output != self._page_evidence:
            details.append(f"{command}: {output}")
        if details:
            self._evidence = "\n".join(details)[:MAX_EVIDENCE_CHARS]

    def _append_metadata(self, details: list[str], value: dict[str, Any]) -> None:
        url = value.get("url")
        if isinstance(url, str):
            self._current_url = url[:2000]
            details.append(f"URL: {self._current_url}")
        title = value.get("title")
        if isinstance(title, str):
            details.append(f"Title: {title[:500]}")
