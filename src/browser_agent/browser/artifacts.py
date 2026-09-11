"""Bounded access to files produced by browser commands."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import BrowserError

MAX_ARTIFACT_TEXT = 12_000
MAX_ARTIFACT_BYTES = 12 * 1024 * 1024
MAX_INLINE_ACTOR_CHARS = 12_000
MAX_SEARCH_HITS = 10
MAX_SEARCH_CHARS = 6_000
TEXT_SUFFIXES = frozenset({".yml", ".yaml", ".txt", ".json"})
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_READABLE_SUFFIXES = TEXT_SUFFIXES | IMAGE_MIME_TYPES.keys()
_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:)?[^\s()<>\"]+\.(?:yml|yaml|txt|json|png|jpe?g|webp)",
    re.IGNORECASE,
)


def extract_paths(value: Any) -> list[str]:
    """Collect unique artifact paths from a nested CLI response in source order."""

    paths: list[str] = []
    seen: set[str] = set()
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            for match in _PATH_PATTERN.findall(item):
                path = match.rstrip(".,;")
                if path not in seen:
                    seen.add(path)
                    paths.append(path)
        elif isinstance(item, dict):
            pending.extend(reversed(tuple(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))
    return paths


class ArtifactStore:
    """Enforce browser-session and per-task evidence filesystem boundaries."""

    def __init__(self, session_root: Path, evidence_root: Path) -> None:
        self.session_root = session_root.resolve()
        self._evidence_root = evidence_root.resolve()

    @property
    def evidence_root(self) -> Path:
        return self._evidence_root

    @evidence_root.setter
    def evidence_root(self, value: Path) -> None:
        root = Path(value).resolve()
        _make_private_directory(root)
        self._evidence_root = root

    def prepare_session(self) -> None:
        _make_private_directory(self.session_root)

    def resolve_read_path(self, value: object) -> Path:
        """Resolve a model-supplied path inside one of the allowed roots."""

        if not isinstance(value, str) or not value.strip():
            raise BrowserError("invalid_artifact_path", "An artifact path is required")
        path = _resolve_path(value, self.session_root)
        if not any(
            _is_within(path, root) for root in (self.session_root, self.evidence_root)
        ):
            raise BrowserError(
                "invalid_artifact_path",
                "Artifact path is outside the browser output directories",
            )
        if path.suffix.lower() not in _READABLE_SUFFIXES:
            raise BrowserError(
                "invalid_artifact_path",
                "Only CLI snapshot and image artifacts can be read",
            )
        return path

    def rewrite_snapshot_args(self, args: list[str]) -> list[str]:
        """Root an optional snapshot filename inside the session directory."""

        return _rewrite_filename(args, self._snapshot_output_path)[0]

    def prepare_screenshot(self, args: list[str]) -> tuple[list[str], Path]:
        """Root a screenshot filename in the task evidence directory."""

        rewritten, path = _rewrite_filename(args, self._screenshot_output_path)
        if path is None:
            _make_private_directory(self.evidence_root)
            path = self.evidence_root / f"screenshot-{uuid4().hex}.png"
            rewritten.append(f"--filename={path}")
        return rewritten, path

    def read(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self.resolve_read_path(args.get("path"))
        offset = args.get("offset", 0)
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or not 0 <= offset <= 1_000_000
        ):
            raise BrowserError(
                "invalid_artifact_offset",
                "Artifact offset must be nonnegative and bounded",
            )

        self._ensure_readable_file(path)
        suffix = path.suffix.lower()
        if suffix in IMAGE_MIME_TYPES:
            return {
                "status": "read",
                "path": str(path),
                "content": [
                    {
                        "type": "image",
                        "data": base64.b64encode(self._read_bytes(path)).decode(
                            "ascii"
                        ),
                        "mimeType": IMAGE_MIME_TYPES[suffix],
                    }
                ],
            }

        text = self._read_text(path)
        chunk = text[offset : offset + MAX_ARTIFACT_TEXT]
        next_offset = offset + len(chunk)
        truncated = next_offset < len(text)
        return {
            "status": "read",
            "path": str(path),
            "text": chunk,
            "offset": offset,
            "next_offset": next_offset if truncated else None,
            "truncated": truncated,
        }

    def search(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self.resolve_read_path(args.get("path"))
        query = args.get("query")
        if not isinstance(query, str) or not query:
            raise BrowserError(
                "invalid_search_query", "A non-empty literal query is required"
            )
        if len(query) > 2_000 or "\x00" in query:
            raise BrowserError("invalid_search_query", "The literal query is too long")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            raise BrowserError(
                "text_artifact_required",
                "Only generated text artifacts can be searched",
            )

        self._ensure_readable_file(path)
        return _search_text(path, query, self._read_text(path))

    def compact_for_actor(
        self, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Replace oversized inline output with a bounded artifact handle."""

        serialized = _serializable_result(command, payload)
        if serialized is None or len(serialized) <= MAX_INLINE_ACTOR_CHARS:
            return payload

        artifact = self._write_text(command, serialized)
        size = len(serialized.encode("utf-8"))
        compact = payload.copy()
        compact["result"] = {
            "artifact": str(artifact),
            "size": size,
            "truncated": True,
            "guidance": (
                "Read with read_browser_artifact using offset, or search with "
                "search_browser_artifact using a literal query."
            ),
        }
        if command == "snapshot":
            compact.pop("snapshot", None)
        compact["artifact_path"] = str(artifact)
        compact["artifact_size"] = size
        return compact

    def snapshot_text(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        fresh_after_ns: int | None = None,
        limit: int,
    ) -> str:
        """Read snapshot text files created by this browser dispatch."""

        chunks: list[str] = []
        chars_used = 0
        for raw_path in extract_paths(_snapshot_sources(command, payload)):
            if chars_used >= limit:
                break
            try:
                path = self.resolve_read_path(raw_path)
                if path.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                self._ensure_readable_file(path)
                if (
                    fresh_after_ns is not None
                    and path.stat().st_mtime_ns <= fresh_after_ns
                ):
                    continue
                remaining = limit - chars_used
                chunk = f"Artifact {path.name}:\n{self._read_text(path)}"[:remaining]
            except (BrowserError, OSError):
                continue
            chunks.append(chunk)
            chars_used += len(chunk) + 2
        return "\n\n".join(chunks)[:limit]

    def screenshot_content(self, path: Path) -> list[dict[str, str]]:
        if not path.is_file():
            raise BrowserError(
                "screenshot_missing",
                "The Playwright CLI did not create the requested screenshot",
            )
        self._ensure_readable_file(path)
        suffix = path.suffix.lower()
        return [
            {
                "type": "image",
                "data": base64.b64encode(self._read_bytes(path)).decode("ascii"),
                "mimeType": IMAGE_MIME_TYPES.get(suffix, "image/png"),
            }
        ]

    def _snapshot_output_path(self, value: str) -> Path:
        path = _resolve_path(value, self.session_root)
        if not _is_within(path, self.session_root):
            raise BrowserError(
                "invalid_artifact_path",
                "Snapshot output must stay in the browser session directory",
            )
        if path.suffix.lower() not in TEXT_SUFFIXES:
            raise BrowserError(
                "invalid_artifact_path", "Snapshot output must be a text artifact"
            )
        _make_private_directory(path.parent)
        return path

    def _screenshot_output_path(self, value: str) -> Path:
        path = _resolve_path(value, self.evidence_root)
        if not _is_within(path, self.evidence_root):
            raise BrowserError(
                "invalid_artifact_path",
                "Screenshot output must stay in the task evidence directory",
            )
        _make_private_directory(path.parent)
        return path

    def _write_text(self, command: str, text: str) -> Path:
        self.prepare_session()
        stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", command)[:40] or "browser"
        path = self.session_root / f"{stem}-{uuid4().hex}.txt"
        try:
            path.write_text(text, encoding="utf-8")
            path.chmod(0o600)
        except OSError as exc:
            raise BrowserError(
                "artifact_write_failed",
                "Could not save the browser result artifact",
                uncertain=True,
            ) from exc
        return path

    @staticmethod
    def _ensure_readable_file(path: Path) -> None:
        if not path.is_file():
            raise BrowserError(
                "artifact_not_found", "The requested browser artifact does not exist"
            )
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise BrowserError(
                "artifact_read_failed", "Could not inspect the browser artifact"
            ) from exc
        if size > MAX_ARTIFACT_BYTES:
            raise BrowserError(
                "artifact_too_large", "The requested browser artifact is too large"
            )

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise BrowserError(
                "artifact_read_failed", "Could not read the browser artifact"
            ) from exc

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise BrowserError(
                "artifact_read_failed", "Could not read the browser artifact"
            ) from exc


def _rewrite_filename(
    args: list[str], resolve: Callable[[str], Path]
) -> tuple[list[str], Path | None]:
    rewritten = args.copy()
    output_path: Path | None = None
    for index, argument in enumerate(rewritten):
        if argument == "--filename" and index + 1 < len(rewritten):
            output_path = resolve(rewritten[index + 1])
            rewritten[index + 1] = str(output_path)
        elif argument.startswith("--filename="):
            output_path = resolve(argument.partition("=")[2])
            rewritten[index] = f"--filename={output_path}"
    return rewritten, output_path


def _serializable_result(command: str, payload: dict[str, Any]) -> str | None:
    if command not in {"snapshot", "find", "eval"}:
        return None
    value: Any = payload.get("result")
    source: Any = value
    if value is None and command == "snapshot" and "snapshot" in payload:
        value = payload["snapshot"]
        source = payload
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(source, ensure_ascii=False, indent=0)
    return None


def _snapshot_sources(command: str, payload: dict[str, Any]) -> list[Any]:
    if command == "snapshot":
        return [payload]
    sources: list[Any] = []
    if "snapshot" in payload:
        sources.append(payload["snapshot"])
    result = payload.get("result")
    if isinstance(result, dict) and "snapshot" in result:
        sources.append(result["snapshot"])
    return sources


def _search_text(path: Path, query: str, text: str) -> dict[str, Any]:
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    cursor = 0
    for line in lines:
        starts.append(cursor)
        cursor += len(line)

    pattern = re.compile(re.escape(query), re.IGNORECASE)
    matches: list[dict[str, Any]] = []
    total = 0
    chars_used = 0
    context_was_cut = False

    for line_index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        for found in pattern.finditer(line):
            total += 1
            if len(matches) >= MAX_SEARCH_HITS or chars_used >= MAX_SEARCH_CHARS:
                continue
            remaining = MAX_SEARCH_CHARS - chars_used
            context, was_cut = _match_context(
                lines, starts, line_index, found.start(), remaining
            )
            matches.append(
                {
                    "line": line_index + 1,
                    "offset": starts[line_index] + found.start(),
                    "text": context,
                }
            )
            chars_used += len(context)
            context_was_cut = context_was_cut or was_cut

    return {
        "status": "searched",
        "path": str(path),
        "query": query,
        "matches": matches,
        "count": total,
        "truncated": context_was_cut or total > len(matches),
    }


def _match_context(
    lines: list[str],
    starts: list[int],
    line_index: int,
    column: int,
    limit: int,
) -> tuple[str, bool]:
    first_line = max(0, line_index - 1)
    last_line = min(len(lines), line_index + 2)
    context = "".join(lines[first_line:last_line]).rstrip("\r\n")
    if len(context) <= limit:
        return context, False

    target = starts[line_index] - starts[first_line] + column
    excerpt_start = max(0, target - limit // 2)
    excerpt_start = min(excerpt_start, len(context) - limit)
    return context[excerpt_start : excerpt_start + limit], True


def _resolve_path(value: str, relative_to: Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else relative_to / path).resolve()


def _make_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents
