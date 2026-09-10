"""Thin transport for the official ``@playwright/cli`` executable.

The browser agent deliberately does not embed Playwright.  A terminal session
owns one persistent Playwright CLI session and every browser operation crosses
that boundary as one ``playwright-cli --json`` subprocess invocation.  The
CLI's JSON is kept intact under ``output``; this module only adds transport
status and, for screenshots, an explicit image content block.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import signal
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


class BrowserError(Exception):
    """Safe transport error crossing the browser boundary."""

    def __init__(self, code: str, message: str, uncertain: bool = False):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.uncertain = bool(uncertain)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "uncertain": self.uncertain,
        }


_COMMANDS = frozenset(
    {
        "open",
        "attach",
        "detach",
        "close",
        "goto",
        "type",
        "click",
        "dblclick",
        "fill",
        "drag",
        "drop",
        "hover",
        "select",
        "upload",
        "check",
        "uncheck",
        "snapshot",
        "find",
        "dialog-accept",
        "dialog-dismiss",
        "resize",
        "go-back",
        "go-forward",
        "reload",
        "press",
        "keydown",
        "keyup",
        "mousemove",
        "mousedown",
        "mouseup",
        "mousewheel",
        "tab-list",
        "tab-new",
        "tab-close",
        "tab-select",
        "screenshot",
        "pdf",
        "console",
        "requests",
        "request",
        "request-headers",
        "request-body",
        "response-headers",
        "response-body",
        "network-state-set",
        "help",
        "list",
    }
)

# The command is deliberately a small public surface.  In particular, eval,
# run-code, storage, cookies, routes, tracing and process controls are not
# exposed to the model.
_READ_ONLY_COMMANDS = frozenset(
    {
        "snapshot",
        "find",
        "tab-list",
        "console",
        "requests",
        "request",
        "request-headers",
        "request-body",
        "response-headers",
        "response-body",
        "help",
        "list",
        "screenshot",
    }
)

_SENSITIVE_ENV_PARTS = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)
_MAX_OUTPUT_CHARS = 40_000
_MAX_EVIDENCE_CHARS = 1_000_000
_MAX_ARTIFACT_TEXT = 12_000
_MAX_ARTIFACT_BYTES = 12 * 1024 * 1024
_MAX_INLINE_ACTOR_CHARS = 12_000
_MAX_SEARCH_HITS = 10
_MAX_SEARCH_CHARS = 6_000
_SNAPSHOT_SUFFIXES = (".yml", ".yaml", ".txt", ".json")
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def read_only(command: str) -> bool:
    """Return whether one official CLI command only inspects browser state."""

    return isinstance(command, str) and command in _READ_ONLY_COMMANDS


def _bounded(value: Any, limit: int = _MAX_OUTPUT_CHARS) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    if isinstance(value, list):
        return [_bounded(item, limit) for item in value[:80]]
    if isinstance(value, dict):
        return {str(key): _bounded(item, limit) for key, item in list(value.items())[:100]}
    return value


def _safe_text(value: Any, limit: int = 1000) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


_HOST_FLAGS = (
    "--config",
    "--session",
    "--profile",
    "--executable-path",
    "--headed",
    "--persistent",
    "--output-dir",
    "--allowed-hosts",
    "--allowed-origins",
    "--blocked-origins",
    "--proxy",
    "--device",
    "--mobile",
)
_COMMAND_FLAGS = {
    "fill": frozenset({"--submit"}),
    "find": frozenset({"--regex"}),
    "snapshot": frozenset({"--depth", "--filename"}),
    "screenshot": frozenset({"--hires"}),
}


class PlaywrightCLI:
    """One persistent official Playwright CLI session.

    The terminal owns one transport object and keeps it alive across
    individual model tasks, so a CLI browser session and its persistent profile
    survive between tasks without any custom page or DOM state in Python.
    """

    COMMANDS = _COMMANDS
    ACTION_COMMANDS = frozenset(_COMMANDS - _READ_ONLY_COMMANDS)

    def __init__(self, settings, *, emit=None, session_name: str | None = None):
        self.settings = settings
        self.emit = emit
        self.session = session_name or f"browser-{uuid4().hex[:12]}"
        root = Path(settings.artifact_dir).resolve()
        self.profile = Path(
            getattr(settings, "browser_profile_dir", None)
            or root / "profiles" / "default"
        ).resolve()
        self._session_dir = (root / "browser" / self.session).resolve()
        self._artifact_dir = (root / "evidence").resolve()
        self._started = False
        self._closed = True
        self._lock = asyncio.Lock()
        self._browser_open = False
        self._attached = False
        self._evidence = ""
        self._page_evidence = ""
        self._current_url: str | None = None
        self.ownership = "attached" if self._cdp_endpoint else "owned"

    @property
    def artifact_dir(self) -> Path:
        return self._artifact_dir

    @artifact_dir.setter
    def artifact_dir(self, value: Path):
        path = Path(value).resolve()
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass
        self._artifact_dir = path

    @property
    def browser_session_dir(self) -> Path:
        return self._session_dir

    @property
    def _cli_command(self) -> str:
        return str(getattr(self.settings, "playwright_cli_command", "npx"))

    @property
    def _cli_package(self) -> str:
        return str(
            getattr(self.settings, "playwright_cli_package", "@playwright/cli@0.1.19")
        )

    @property
    def _headed(self) -> bool:
        return bool(getattr(self.settings, "browser_headed", True))

    @property
    def _cdp_endpoint(self) -> str | None:
        value = getattr(self.settings, "browser_cdp_endpoint", None)
        if value is None:
            value = os.getenv("AGENT_BROWSER_CDP_ENDPOINT")
        return str(value).strip() if value else None

    @property
    def current_url(self) -> str | None:
        return self._current_url

    @property
    def is_open(self) -> bool:
        return self._started and not self._closed

    @property
    def attached(self) -> bool:
        return self._attached

    @property
    def evidence(self) -> str:
        return self._evidence

    @property
    def instructions(self) -> str:
        path = Path(__file__).with_name("playwright_skill.md")
        try:
            return path.read_text(encoding="utf-8")[:_MAX_EVIDENCE_CHARS]
        except OSError:
            return (
                "Use the playwright tool with one official Playwright CLI command at a time. "
                "Use snapshot or find before acting and read_browser_artifact for explicit files."
            )

    @staticmethod
    def read_only(command: str) -> bool:
        return read_only(command)

    def _ensure_started(self):
        if not self.is_open:
            raise BrowserError("browser_not_started", "The Playwright CLI session is not open")

    def _safe_env(self) -> dict[str, str]:
        env = {}
        for key, value in os.environ.items():
            upper = key.upper()
            if any(part in upper for part in _SENSITIVE_ENV_PARTS):
                continue
            env[key] = value
        env.pop("OPENAI_API_KEY", None)
        env.pop("AGENT_BROWSER_CDP_ENDPOINT", None)
        return env

    def _argv(self, command: str, args: list[str]) -> list[str]:
        executable = Path(self._cli_command).name
        if executable == "npx":
            argv = [self._cli_command, "--yes", self._cli_package]
        else:
            argv = [self._cli_command]
        argv += [f"-s={self.session}"]
        if command == "help":
            argv.append("--help")
            argv += args
        else:
            argv += [command, *args]
        argv.append("--json")
        return argv

    @staticmethod
    def _decode_json(text: str, command: str) -> Any:
        text = text.strip()
        if not text:
            if command == "help":
                return {"result": ""}
            raise BrowserError(
                "cli_parse_error",
                "The Playwright CLI returned no JSON output",
                uncertain=True,
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # ``help`` is the only documented plain-text command.  Every
            # browser operation requested by the actor must use structured JSON
            # so a partial/trailing CLI response cannot look successful.
            if command == "help":
                return {"result": text[:_MAX_OUTPUT_CHARS]}
            raise BrowserError(
                "cli_parse_error",
                "The Playwright CLI returned invalid JSON",
                uncertain=True,
            )

    async def _run_cli(self, command: str, args: list[str]) -> dict[str, Any]:
        self._session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._session_dir.chmod(0o700)
        argv = self._argv(command, args)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self._session_dir),
                env=self._safe_env(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise BrowserError("cli_unavailable", "Could not start the Playwright CLI") from exc
        communication = asyncio.create_task(process.communicate())
        try:
            stdout, stderr = await asyncio.shield(communication)
        except asyncio.CancelledError:
            try:
                process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=1)
            except (TimeoutError, ProcessLookupError):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                stdout, stderr = await asyncio.shield(communication)
            except (asyncio.CancelledError, ProcessLookupError):
                stdout, stderr = b"", b""
            log_paths = self._log_process_output(command, stdout, stderr)
            self._emit_diagnostic(
                command,
                process.returncode,
                len(stdout),
                len(stderr),
                "cancelled",
                log_paths,
            )
            raise
        stderr_text = stderr.decode("utf-8", errors="replace")
        stdout_text = stdout.decode("utf-8", errors="replace")
        log_paths = self._log_process_output(command, stdout, stderr)
        try:
            parsed = self._decode_json(stdout_text, command)
        except BrowserError:
            self._emit_diagnostic(
                command,
                process.returncode,
                len(stdout_text),
                len(stderr_text),
                "parse_error",
                log_paths,
            )
            raise
        if not isinstance(parsed, dict):
            parsed = {"result": parsed}
        self._emit_diagnostic(
            command,
            process.returncode,
            len(stdout_text),
            len(stderr_text),
            "error" if parsed.get("isError") is True else "ok",
            log_paths,
        )
        if parsed.get("isError") is True:
            raise BrowserError("cli_error", _safe_text(parsed.get("error") or parsed.get("result")))
        if process.returncode not in (0, None):
            detail = parsed.get("error") or stderr_text or f"exit code {process.returncode}"
            raise BrowserError("cli_failed", _safe_text(detail))
        return parsed

    def _emit_diagnostic(
        self,
        command: str,
        exit_code: int | None,
        stdout_chars: int,
        stderr_chars: int,
        status: str,
        log_paths: dict[str, str] | None = None,
    ):
        if self.emit is None:
            return
        self.emit(
            "diagnostic",
            {
                "phase": "playwright_cli",
                "command": command,
                "exit_code": exit_code,
                "stdout_chars": stdout_chars,
                "stderr_chars": stderr_chars,
                "status": status,
                "stdout_path": (log_paths or {}).get("stdout"),
                "stderr_path": (log_paths or {}).get("stderr"),
            },
        )

    def _log_process_output(self, command: str, stdout: bytes, stderr: bytes) -> dict[str, str]:
        """Keep raw CLI streams private for diagnosing transport failures."""

        stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", command)[:40] + "-" + uuid4().hex[:8]
        paths = {}
        for suffix, data in (("stdout", stdout), ("stderr", stderr)):
            path = self._session_dir / f"{stem}-{suffix}.log"
            try:
                path.write_bytes(data[-100_000:])
                path.chmod(0o600)
                paths[suffix] = str(path)
            except OSError:
                continue
        return paths

    def _artifact_path(self, value: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise BrowserError("invalid_artifact_path", "An artifact path is required")
        raw = Path(value)
        path = raw if raw.is_absolute() else self._session_dir / raw
        path = path.resolve()
        roots = (self._session_dir, self._artifact_dir)
        if not any(path == root or root in path.parents for root in roots):
            raise BrowserError("invalid_artifact_path", "Artifact path is outside the browser output directories")
        if path.suffix.lower() not in _SNAPSHOT_SUFFIXES + tuple(_IMAGE_TYPES):
            raise BrowserError("invalid_artifact_path", "Only CLI snapshot and image artifacts can be read")
        return path

    @staticmethod
    def _extract_paths(value: Any) -> list[str]:
        paths: list[str] = []
        if isinstance(value, str):
            for match in re.findall(
                r"(?:[A-Za-z]:)?[^\s()<>\"]+\.(?:yml|yaml|txt|json|png|jpe?g|webp)",
                value,
                re.IGNORECASE,
            ):
                paths.append(match.rstrip(".,;"))
        elif isinstance(value, dict):
            for item in value.values():
                paths.extend(PlaywrightCLI._extract_paths(item))
        elif isinstance(value, list):
            for item in value:
                paths.extend(PlaywrightCLI._extract_paths(item))
        return list(dict.fromkeys(paths))

    def _read_snapshot_for_evidence(
        self,
        payload: dict[str, Any],
        *,
        command: str | None = None,
        fresh_after_ns: int | None = None,
    ) -> str:
        # ``read_browser_artifact`` never calls this helper.  For a browser
        # command, inspect every upstream snapshot envelope because actions
        # such as click/fill can return a newly generated snapshot file too.
        # A timestamp guard keeps a stale path echoed by a result from
        # replacing current evidence.  Do not scan arbitrary prose for old
        # ``.yml`` strings: only snapshot-bearing fields are eligible.
        sources: list[Any] = []
        if command == "snapshot":
            sources.append(payload)
        else:
            if "snapshot" in payload:
                sources.append(payload["snapshot"])
            result = payload.get("result")
            if isinstance(result, dict) and "snapshot" in result:
                sources.append(result["snapshot"])
        raw_paths: list[str] = []
        for source in sources:
            raw_paths.extend(self._extract_paths(source))
        raw_paths = list(dict.fromkeys(raw_paths))
        chunks: list[str] = []
        for raw_path in raw_paths:
            try:
                path = self._artifact_path(raw_path)
            except BrowserError:
                continue
            if path.suffix.lower() not in _SNAPSHOT_SUFFIXES or not path.is_file():
                continue
            try:
                if (
                    fresh_after_ns is not None
                    and path.stat().st_mtime_ns <= fresh_after_ns
                ):
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunks.append(f"Artifact {path.name}:\n{text[:_MAX_EVIDENCE_CHARS]}")
            if sum(len(chunk) for chunk in chunks) >= _MAX_EVIDENCE_CHARS:
                break
        return "\n\n".join(chunks)[:_MAX_EVIDENCE_CHARS]

    @staticmethod
    def _snapshot_scope_target(args: list[str]) -> str | None:
        skip_next = False
        for value in args:
            if skip_next:
                skip_next = False
                continue
            if value in {"--depth", "--filename"}:
                skip_next = True
                continue
            if value.startswith("--"):
                continue
            return value
        return None

    def _update_evidence(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        fresh_after_ns: int | None = None,
    ):
        snapshot = self._read_snapshot_for_evidence(
            payload, command=command, fresh_after_ns=fresh_after_ns
        )
        result = payload.get("result")
        if result is None and any(key in payload for key in ("snapshot", "url", "title")):
            result = {
                key: payload[key]
                for key in ("snapshot", "url", "title")
                if key in payload
            }
        output = result if isinstance(result, str) else ""
        if snapshot:
            output = snapshot
        elif isinstance(result, (dict, list)):
            # Preserve the complete upstream structure until the explicit
            # evidence bound.  Do not apply the transport output list/dict
            # caps here: late refs in a long snapshot are still useful to a
            # reviewer.
            output = json.dumps(result, ensure_ascii=False)

        # A snapshot command (including an inline snapshot array) replaces the
        # cached page evidence.  ``find`` contributes a compact excerpt while
        # retaining the latest full snapshot.  Every other command leaves the
        # page cache untouched so a screenshot, help output, or artifact path
        # cannot hide the target the reviewer just saw.
        inline_snapshot = False
        snapshot_value = payload.get("snapshot")
        if snapshot_value is None and isinstance(result, dict):
            snapshot_value = result.get("snapshot")
        if snapshot_value is not None:
            # A file descriptor points at an artifact handled above.  Inline
            # arrays/objects/text are actual current-page evidence; a stale
            # file path alone must leave the previous cache untouched.
            inline_snapshot = not (
                isinstance(snapshot_value, dict)
                and set(snapshot_value).issubset({"file", "path"})
            )
        if command == "snapshot" and output and snapshot_value is None:
            inline_snapshot = True
        if snapshot or inline_snapshot:
            self._page_evidence = output[:_MAX_EVIDENCE_CHARS]
        elif command == "find" and output:
            find_excerpt = f"Find result: {output}"
            if self._page_evidence:
                self._page_evidence = (
                    self._page_evidence + "\n" + find_excerpt
                )[:_MAX_EVIDENCE_CHARS]
            else:
                self._page_evidence = find_excerpt[:_MAX_EVIDENCE_CHARS]

        details = []
        if isinstance(payload.get("url"), str):
            details.append(f"URL: {payload['url'][:2000]}")
            self._current_url = payload["url"][:2000]
        if isinstance(payload.get("title"), str):
            details.append(f"Title: {payload['title'][:500]}")
        if isinstance(result, dict):
            if isinstance(result.get("url"), str):
                details.append(f"URL: {result['url'][:2000]}")
                self._current_url = result["url"][:2000]
            if isinstance(result.get("title"), str):
                details.append(f"Title: {result['title'][:500]}")
        if self._page_evidence:
            details.append(f"Page evidence: {self._page_evidence}")
        if output and output != self._page_evidence:
            details.append(f"{command}: {output}")
        if details:
            self._evidence = "\n".join(details)[:_MAX_EVIDENCE_CHARS]

    def _write_generated_text_artifact(self, command: str, text: str) -> Path:
        self._session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        safe_command = re.sub(r"[^a-zA-Z0-9_.-]", "_", command)[:40] or "browser"
        path = self._session_dir / f"{safe_command}-{uuid4().hex}.txt"
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

    def _compact_actor_payload(
        self, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist oversized inline snapshot/find output before actor delivery."""

        if command not in {"snapshot", "find"}:
            return payload
        value = payload.get("result")
        source: Any = value
        if value is None and command == "snapshot" and "snapshot" in payload:
            source = payload
            value = payload["snapshot"]
        if value is None:
            return payload
        if isinstance(value, str):
            serialized = value
        elif isinstance(value, (dict, list)):
            serialized = json.dumps(source, ensure_ascii=False, indent=2)
        else:
            return payload
        if len(serialized) <= _MAX_INLINE_ACTOR_CHARS:
            return payload
        artifact = self._write_generated_text_artifact(command, serialized)
        size = len(serialized.encode("utf-8"))
        compact = dict(payload)
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

    async def _search_artifact(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._artifact_path(args.get("path"))
        query = args.get("query")
        if not isinstance(query, str) or not query:
            raise BrowserError("invalid_search_query", "A non-empty literal query is required")
        if len(query) > 2000 or "\x00" in query:
            raise BrowserError("invalid_search_query", "The literal query is too long")
        if path.suffix.lower() not in _SNAPSHOT_SUFFIXES:
            raise BrowserError("text_artifact_required", "Only generated text artifacts can be searched")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise BrowserError("artifact_read_failed", "Could not inspect the browser artifact") from exc
        if size > _MAX_ARTIFACT_BYTES:
            raise BrowserError("artifact_too_large", "The requested browser artifact is too large")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise BrowserError("artifact_read_failed", "Could not read the browser artifact") from exc

        needle = query.casefold()
        matches: list[dict[str, Any]] = []
        total = 0
        excerpt_chars = 0
        line_start = 0
        truncated = False
        for line_number, raw_line in enumerate(text.splitlines(keepends=True), 1):
            line = raw_line.rstrip("\r\n")
            folded = line.casefold()
            match_at = folded.find(needle)
            if match_at < 0:
                line_start += len(raw_line)
                continue
            total += 1
            if len(matches) < _MAX_SEARCH_HITS and excerpt_chars < _MAX_SEARCH_CHARS:
                remaining = _MAX_SEARCH_CHARS - excerpt_chars
                excerpt = line[:remaining]
                if len(excerpt) < len(line):
                    truncated = True
                matches.append(
                    {
                        "line": line_number,
                        "offset": line_start + match_at,
                        "text": excerpt,
                    }
                )
                excerpt_chars += len(excerpt)
            else:
                truncated = True
            line_start += len(raw_line)
        if total > len(matches):
            truncated = True
        return {
            "status": "searched",
            "path": str(path),
            "query": query,
            "matches": matches,
            "count": total,
            "truncated": truncated,
        }

    def _screenshot_args(self, args: list[str]) -> tuple[list[str], Path]:
        path: Path | None = None
        rewritten = list(args)
        for index, value in enumerate(rewritten):
            if value == "--filename" and index + 1 < len(rewritten):
                path = self._artifact_path_for_screenshot(rewritten[index + 1])
                rewritten[index + 1] = str(path)
            elif value.startswith("--filename="):
                path = self._artifact_path_for_screenshot(value.split("=", 1)[1])
                rewritten[index] = f"--filename={path}"
        if path is None:
            self._artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self._artifact_dir / f"screenshot-{uuid4().hex}.png"
            rewritten.append(f"--filename={path}")
        return rewritten, path

    def _snapshot_args(self, args: list[str]) -> list[str]:
        rewritten = list(args)
        for index, value in enumerate(rewritten):
            if value == "--filename" and index + 1 < len(rewritten):
                rewritten[index + 1] = str(self._snapshot_path(rewritten[index + 1]))
            elif value.startswith("--filename="):
                rewritten[index] = f"--filename={self._snapshot_path(value.split('=', 1)[1])}"
        return rewritten

    def _snapshot_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self._session_dir / path
        path = path.resolve()
        if path != self._session_dir and self._session_dir not in path.parents:
            raise BrowserError("invalid_artifact_path", "Snapshot output must stay in the browser session directory")
        if path.suffix.lower() not in _SNAPSHOT_SUFFIXES:
            raise BrowserError("invalid_artifact_path", "Snapshot output must be a text artifact")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def _artifact_path_for_screenshot(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self._artifact_dir / path
        path = path.resolve()
        if self._artifact_dir != path and self._artifact_dir not in path.parents:
            raise BrowserError("invalid_artifact_path", "Screenshot output must stay in the task evidence directory")
        return path

    def _open_args(self, args: list[str]) -> list[str]:
        """Apply host profile/display defaults to an actor's ``open`` call."""

        rewritten = list(args)
        if not any(value == "--persistent" or value.startswith("--persistent=") for value in rewritten):
            rewritten.append("--persistent")
        if not any(value == "--profile" or value.startswith("--profile=") for value in rewritten):
            rewritten.append(f"--profile={self.profile}")
        if self._headed and not any(
            value == "--headed" or value.startswith("--headed=") for value in rewritten
        ):
            rewritten.append("--headed")
        browser_channel = getattr(self.settings, "browser_channel", None)
        if browser_channel and not any(value == "--browser" or value.startswith("--browser=") for value in rewritten):
            rewritten.append(f"--browser={browser_channel}")
        return rewritten

    def _attach_args(self, args: list[str]) -> list[str]:
        rewritten = list(args)
        if self._cdp_endpoint and not any(value == "--cdp" or value.startswith("--cdp=") for value in rewritten):
            rewritten.append(f"--cdp={self._cdp_endpoint}")
        return rewritten

    @staticmethod
    def _validate_model_args(command: str, args: list[str]):
        allowed = _COMMAND_FLAGS.get(command, frozenset())
        for index, value in enumerate(args):
            if not value.startswith("-") or re.fullmatch(r"-\d+(?:\.\d+)?", value):
                continue
            if command == "attach" and (
                value in {"--cdp", "--extension"}
                or value.startswith(("--cdp=", "--extension="))
            ):
                continue
            if command == "open" and (
                value == "--browser" or value.startswith("--browser=")
            ):
                continue
            if any(value == flag or value.startswith(flag + "=") for flag in _HOST_FLAGS):
                raise BrowserError("forbidden_argument", "Host and session flags are controlled by the browser agent")
            if value not in allowed and not any(value.startswith(flag + "=") for flag in allowed):
                raise BrowserError("forbidden_argument", f"The {command} command does not accept that flag")
            if value in {"--regex", "--depth"} and index + 1 >= len(args):
                raise BrowserError("invalid_arguments", f"The {value} flag requires a value")

    async def _read_artifact(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._artifact_path(args.get("path"))
        offset = args.get("offset", 0)
        if not isinstance(offset, int) or offset < 0 or offset > 1_000_000:
            raise BrowserError("invalid_artifact_offset", "Artifact offset must be nonnegative and bounded")
        if not path.is_file():
            raise BrowserError("artifact_not_found", "The requested browser artifact does not exist")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise BrowserError("artifact_read_failed", "Could not inspect the browser artifact") from exc
        if size > _MAX_ARTIFACT_BYTES:
            raise BrowserError("artifact_too_large", "The requested browser artifact is too large")
        suffix = path.suffix.lower()
        if suffix in _IMAGE_TYPES:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            return {
                "status": "read",
                "path": str(path),
                "content": [{"type": "image", "data": data, "mimeType": _IMAGE_TYPES[suffix]}],
            }
        text = path.read_text(encoding="utf-8", errors="replace")
        chunk = text[offset : offset + _MAX_ARTIFACT_TEXT]
        end = offset + len(chunk)
        return {
            "status": "read",
            "path": str(path),
            "text": chunk,
            "offset": offset,
            "next_offset": end if end < len(text) else None,
            "truncated": end < len(text),
        }

    async def start(self):
        async with self._lock:
            if self._started and not self._closed:
                raise BrowserError("already_started", "The Playwright CLI session is already running")
            self.settings.prepare()
            self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.profile.chmod(0o700)
            self._session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._started = True
            self._closed = False
            self._browser_open = False

    async def close(self):
        async with self._lock:
            if not self._started or self._closed:
                return
            if self._browser_open:
                command = "detach" if self._attached else "close"
                try:
                    await self._run_cli(command, [])
                except BrowserError:
                    # Closing is best effort; the session is marked closed so a
                    # failed CLI teardown can never be accidentally reused.
                    pass
            self._closed = True
            self._started = False
            self._browser_open = False

    async def prepare_task(self):
        """Clear task evidence without launching a browser or reading a page."""

        self._evidence = ""
        self._page_evidence = ""

    async def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        command: str | None = None
        try:
            self._ensure_started()
            if not isinstance(args, dict):
                raise BrowserError("invalid_arguments", "Browser command arguments must be an object")
            if tool == "playwright":
                command = args.get("command")
                raw_args = args.get("args", [])
                if not isinstance(command, str) or command not in _COMMANDS:
                    raise BrowserError("unsupported_command", "That Playwright CLI command is not available")
                if not isinstance(raw_args, list) or any(not isinstance(value, str) for value in raw_args):
                    raise BrowserError("invalid_arguments", "Playwright CLI args must be a list of strings")
                if len(raw_args) > 32 or any(len(value) > 8000 or "\x00" in value for value in raw_args):
                    raise BrowserError("invalid_arguments", "Playwright CLI args are too long")
                cli_args = list(raw_args)
                self._validate_model_args(command, cli_args)
                if command == "open":
                    cli_args = self._open_args(cli_args)
                elif command == "attach":
                    cli_args = self._attach_args(cli_args)
                elif command == "snapshot":
                    cli_args = self._snapshot_args(cli_args)
                screenshot_path = None
                if command == "screenshot":
                    cli_args, screenshot_path = self._screenshot_args(cli_args)
                async with self._lock:
                    # An open/attach process may create a browser before its
                    # navigation or handshake reports an error.  Remember the
                    # attempted ownership before dispatch so /exit still
                    # cleans up this named session without touching others.
                    if command in {"open", "attach"}:
                        self._browser_open = True
                        self._attached = command == "attach"
                    dispatch_ns = time.time_ns()
                    payload = await self._run_cli(command, cli_args)
                    self._update_evidence(
                        command, payload, fresh_after_ns=dispatch_ns
                    )
                    actor_payload = self._compact_actor_payload(command, payload)
                    if command == "snapshot":
                        scope_target = self._snapshot_scope_target(cli_args)
                        if scope_target:
                            actor_payload = dict(actor_payload)
                            actor_payload["scope_hint"] = {
                                "target": scope_target,
                                "warning": (
                                    "This targeted snapshot replaces the active reference "
                                    "scope. Use a full snapshot or find before using refs "
                                    "outside this target."
                                ),
                            }
                    if command in {"open", "attach"}:
                        self._browser_open = True
                        self._attached = command == "attach"
                    elif command in {"close", "detach"}:
                        self._browser_open = False
                result: dict[str, Any] = {
                    "status": "executed",
                    "tool": tool,
                    "command": command,
                    "output": actor_payload,
                }
                if screenshot_path is not None and screenshot_path.is_file():
                    result["content"] = [
                        {
                            "type": "image",
                            "data": base64.b64encode(screenshot_path.read_bytes()).decode("ascii"),
                            "mimeType": _IMAGE_TYPES.get(screenshot_path.suffix.lower(), "image/png"),
                        }
                    ]
                elif screenshot_path is not None:
                    raise BrowserError(
                        "screenshot_missing",
                        "The Playwright CLI did not create the requested screenshot",
                    )
                return result
            if tool == "read_browser_artifact":
                async with self._lock:
                    return await self._read_artifact(args)
            if tool == "search_browser_artifact":
                async with self._lock:
                    return await self._search_artifact(args)
            raise BrowserError(
                "unknown_tool",
                "Only playwright, read_browser_artifact, and search_browser_artifact are available",
            )
        except BrowserError as exc:
            return {
                "status": "error",
                "tool": tool,
                **({"command": command} if command else {}),
                "error": exc.as_dict(),
            }
