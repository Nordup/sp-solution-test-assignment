"""Fresh browser evidence retained for the independent action reviewer."""

from __future__ import annotations

import json
from typing import Any

from .artifacts import ArtifactStore, extract_paths

MAX_EVIDENCE_CHARS = 1_000_000


class EvidenceCache:
    """Track current page evidence separately from the latest command output."""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts
        self._text = ""
        self._page = ""
        self._current_url: str | None = None

    @property
    def text(self) -> str:
        return self._text

    @property
    def current_url(self) -> str | None:
        return self._current_url

    def clear(self) -> None:
        """Clear task evidence while retaining the browser's current URL."""

        self._text = ""
        self._page = ""

    def update(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        fresh_after_ns: int | None = None,
    ) -> None:
        """Incorporate one successful CLI response into reviewer evidence."""

        result = _response_result(payload)
        output = _render(result)
        snapshot = self._artifacts.snapshot_text(
            command,
            payload,
            fresh_after_ns=fresh_after_ns,
            limit=MAX_EVIDENCE_CHARS,
        )

        snapshot_value = _snapshot_value(payload, result)
        has_inline_snapshot = _is_inline_snapshot(snapshot_value)
        if command == "snapshot" and output and snapshot_value is None:
            has_inline_snapshot = True

        if snapshot:
            self._page = snapshot[:MAX_EVIDENCE_CHARS]
        elif has_inline_snapshot:
            self._page = output[:MAX_EVIDENCE_CHARS]
        elif command == "find" and output:
            self._append_find_result(output)

        details = self._metadata(payload)
        if isinstance(result, dict):
            details.extend(self._metadata(result))
        if self._page:
            details.append(f"Page evidence: {self._page}")
        if output and output != self._page:
            details.append(f"{command}: {output}")
        if details:
            self._text = "\n".join(details)[:MAX_EVIDENCE_CHARS]

    def _append_find_result(self, output: str) -> None:
        find_result = f"Find result: {output}"
        if self._page:
            find_result = f"{self._page}\n{find_result}"
        self._page = find_result[:MAX_EVIDENCE_CHARS]

    def _metadata(self, value: dict[str, Any]) -> list[str]:
        details: list[str] = []
        url = value.get("url")
        if isinstance(url, str):
            self._current_url = url[:2_000]
            details.append(f"URL: {self._current_url}")
        title = value.get("title")
        if isinstance(title, str):
            details.append(f"Title: {title[:500]}")
        return details


def _response_result(payload: dict[str, Any]) -> Any:
    result = payload.get("result")
    if result is not None:
        return result
    metadata = {
        key: payload[key] for key in ("snapshot", "url", "title") if key in payload
    }
    return metadata or None


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return ""


def _snapshot_value(payload: dict[str, Any], result: Any) -> Any:
    snapshot = payload.get("snapshot")
    if snapshot is None and isinstance(result, dict):
        snapshot = result.get("snapshot")
    return snapshot


def _is_inline_snapshot(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict) and set(value).issubset({"file", "path"}):
        return False
    return not (isinstance(value, str) and extract_paths(value))
