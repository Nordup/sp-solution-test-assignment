"""Playwright CLI session lifecycle and subprocess transport."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import Settings
from .artifacts import ArtifactStore
from .commands import (
    ACTION_COMMANDS,
    COMMANDS,
    build_argv,
    is_read_only,
    snapshot_scope_target,
    validate_invocation,
    with_attach_defaults,
    with_open_defaults,
)
from .errors import BrowserError
from .evidence import MAX_EVIDENCE_CHARS, EvidenceCache

_SENSITIVE_ENV_PARTS = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)
_MAX_OUTPUT_CHARS = 40_000
_MAX_PRIVATE_LOG_BYTES = 100_000
Emit = Callable[[str, Any], None]


class _Connection(Enum):
    OWNED = "owned"
    ATTACHED = "attached"


class PlaywrightCLI:
    """Manage one reusable session backed by the official Playwright CLI."""

    COMMANDS = COMMANDS
    ACTION_COMMANDS = ACTION_COMMANDS

    def __init__(
        self,
        settings: Settings,
        *,
        emit: Emit | None = None,
        session_name: str | None = None,
    ) -> None:
        self.settings = settings
        self.emit = emit
        self.session = session_name or f"browser-{uuid4().hex[:12]}"

        root = settings.artifact_dir.resolve()
        profile = settings.browser_profile_dir or root / "profiles" / "default"
        self.profile = profile.resolve()
        self._artifacts = ArtifactStore(
            session_root=root / "browser" / self.session,
            evidence_root=root / "evidence",
        )
        self._evidence = EvidenceCache(self._artifacts)
        self._running = False
        self._connection: _Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def artifact_dir(self) -> Path:
        return self._artifacts.evidence_root

    @artifact_dir.setter
    def artifact_dir(self, value: Path) -> None:
        self._artifacts.evidence_root = value

    @property
    def browser_session_dir(self) -> Path:
        return self._artifacts.session_root

    @property
    def current_url(self) -> str | None:
        return self._evidence.current_url

    @property
    def is_open(self) -> bool:
        return self._running

    @property
    def attached(self) -> bool:
        return self._connection is _Connection.ATTACHED

    @property
    def ownership(self) -> str:
        if self._connection is not None:
            return self._connection.value
        return (
            _Connection.ATTACHED.value
            if self._cdp_endpoint
            else _Connection.OWNED.value
        )

    @property
    def evidence(self) -> str:
        return self._evidence.text

    @property
    def instructions(self) -> str:
        path = Path(__file__).parent.parent / "playwright_skill.md"
        try:
            return path.read_text(encoding="utf-8")[:MAX_EVIDENCE_CHARS]
        except OSError:
            return (
                "Use the playwright tool with one official Playwright CLI command at a time. "
                "Use snapshot or find before acting and read_browser_artifact for explicit files."
            )

    @staticmethod
    def is_read_only(command: str) -> bool:
        return is_read_only(command)

    async def start(self) -> None:
        """Prepare private storage without opening or attaching a browser."""

        async with self._lock:
            if self._running:
                raise BrowserError(
                    "already_started", "The Playwright CLI session is already running"
                )
            self.settings.prepare()
            self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                self.profile.chmod(0o700)
            except OSError:
                pass
            self._artifacts.prepare_session()
            self._connection = None
            self._running = True

    async def close(self) -> None:
        """Best-effort teardown without closing an attached external browser."""

        async with self._lock:
            if not self._running:
                return
            try:
                if self._connection is not None:
                    command = "detach" if self.attached else "close"
                    try:
                        await self._invoke_cli(command, [])
                    except BrowserError:
                        pass
            finally:
                self._connection = None
                self._running = False

    async def prepare_task(self) -> None:
        """Clear prior task evidence without changing the browser page."""

        async with self._lock:
            self._evidence.clear()

    async def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute one model-facing browser tool call."""

        command: str | None = None
        try:
            self._ensure_started()
            if not isinstance(args, dict):
                raise BrowserError(
                    "invalid_arguments", "Browser command arguments must be an object"
                )
            if tool == "playwright":
                command, cli_args = validate_invocation(
                    args.get("command"), args.get("args", [])
                )
                return await self._execute_playwright(command, cli_args)
            if tool == "read_browser_artifact":
                async with self._lock:
                    return self._artifacts.read(args)
            if tool == "search_browser_artifact":
                async with self._lock:
                    return self._artifacts.search(args)
            raise BrowserError(
                "unknown_tool",
                "Only playwright, read_browser_artifact, and search_browser_artifact are available",
            )
        except BrowserError as exc:
            result: dict[str, Any] = {
                "status": "error",
                "tool": tool,
                "error": exc.as_dict(),
            }
            if command is not None:
                result["command"] = command
            return result

    async def _execute_playwright(
        self, command: str, args: list[str]
    ) -> dict[str, Any]:
        cli_args, screenshot_path = self._prepare_command(command, args)
        async with self._lock:
            self._ensure_started()
            self._record_dispatch(command)
            dispatched_at_ns = time.time_ns()
            payload = await self._invoke_cli(command, cli_args)
            self._evidence.update(
                command,
                payload,
                fresh_after_ns=dispatched_at_ns,
            )
            actor_payload = self._artifacts.compact_for_actor(command, payload)
            actor_payload = _with_snapshot_scope(command, cli_args, actor_payload)
            self._record_success(command)

            result: dict[str, Any] = {
                "status": "executed",
                "tool": "playwright",
                "command": command,
                "output": actor_payload,
            }
            if screenshot_path is not None:
                result["content"] = self._artifacts.screenshot_content(screenshot_path)
            return result

    def _prepare_command(
        self,
        command: str,
        args: list[str],
    ) -> tuple[list[str], Path | None]:
        if command == "open":
            return (
                with_open_defaults(
                    args,
                    profile=self.profile,
                    headed=self.settings.browser_headed,
                    browser_channel=self.settings.browser_channel,
                ),
                None,
            )
        if command == "attach":
            return with_attach_defaults(args, self._cdp_endpoint), None
        if command == "snapshot":
            return self._artifacts.rewrite_snapshot_args(args), None
        if command == "screenshot":
            return self._artifacts.prepare_screenshot(args)
        return args, None

    def _record_dispatch(self, command: str) -> None:
        # Opening or attaching may have taken effect even if its response is lost.
        if command == "open":
            self._connection = _Connection.OWNED
        elif command == "attach":
            self._connection = _Connection.ATTACHED

    def _record_success(self, command: str) -> None:
        if command in {"close", "detach"}:
            self._connection = None

    async def _invoke_cli(self, command: str, args: list[str]) -> dict[str, Any]:
        """Run one literal subprocess and decode its native JSON envelope."""

        self._artifacts.prepare_session()
        argv = build_argv(
            self.settings.playwright_cli_command,
            self.settings.playwright_cli_package,
            self.session,
            command,
            args,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self.browser_session_dir),
                env=_safe_subprocess_env(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise BrowserError(
                "cli_unavailable", "Could not start the Playwright CLI"
            ) from exc

        communication = asyncio.create_task(process.communicate())
        try:
            stdout, stderr = await asyncio.shield(communication)
        except asyncio.CancelledError:
            stdout, stderr = await self._reap_cancelled_process(process, communication)
            log_paths = self._log_process_output(command, stdout, stderr)
            self._emit_diagnostic(
                command=command,
                exit_code=process.returncode,
                stdout_chars=len(stdout),
                stderr_chars=len(stderr),
                status="cancelled",
                log_paths=log_paths,
            )
            raise

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        log_paths = self._log_process_output(command, stdout, stderr)
        try:
            parsed = _decode_json(stdout_text, command)
        except BrowserError:
            self._emit_diagnostic(
                command=command,
                exit_code=process.returncode,
                stdout_chars=len(stdout_text),
                stderr_chars=len(stderr_text),
                status="parse_error",
                log_paths=log_paths,
            )
            raise

        if not isinstance(parsed, dict):
            parsed = {"result": parsed}
        nested_result = parsed.get("result")
        nested_error = (
            isinstance(nested_result, dict) and nested_result.get("isError") is True
        )
        cli_error = parsed.get("isError") is True or nested_error
        self._emit_diagnostic(
            command=command,
            exit_code=process.returncode,
            stdout_chars=len(stdout_text),
            stderr_chars=len(stderr_text),
            status=(
                "error" if cli_error or process.returncode not in (0, None) else "ok"
            ),
            log_paths=log_paths,
        )

        if cli_error:
            detail = parsed.get("error")
            if not detail and nested_error:
                detail = nested_result.get("error")
            raise BrowserError(
                "cli_error",
                _safe_text(detail or parsed.get("result")),
                uncertain=not is_read_only(command),
            )
        if process.returncode not in (0, None):
            detail = (
                parsed.get("error") or stderr_text or f"exit code {process.returncode}"
            )
            raise BrowserError(
                "cli_failed",
                _safe_text(detail),
                uncertain=not is_read_only(command),
            )
        return parsed

    async def _reap_cancelled_process(
        self,
        process: asyncio.subprocess.Process,
        communication: asyncio.Task[tuple[bytes, bytes]],
    ) -> tuple[bytes, bytes]:
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
                await asyncio.wait_for(process.wait(), timeout=1)
            except (TimeoutError, ProcessLookupError):
                pass

        try:
            return await asyncio.shield(communication)
        except (asyncio.CancelledError, ProcessLookupError):
            return b"", b""

    def _log_process_output(
        self,
        command: str,
        stdout: bytes,
        stderr: bytes,
    ) -> dict[str, str]:
        """Keep bounded raw streams private for transport diagnostics."""

        safe_command = re.sub(r"[^a-zA-Z0-9_.-]", "_", command)[:40]
        stem = f"{safe_command}-{uuid4().hex[:8]}"
        paths: dict[str, str] = {}
        for stream, data in (("stdout", stdout), ("stderr", stderr)):
            path = self.browser_session_dir / f"{stem}-{stream}.log"
            try:
                path.write_bytes(data[-_MAX_PRIVATE_LOG_BYTES:])
                path.chmod(0o600)
            except OSError:
                continue
            paths[stream] = str(path)
        return paths

    def _emit_diagnostic(
        self,
        *,
        command: str,
        exit_code: int | None,
        stdout_chars: int,
        stderr_chars: int,
        status: str,
        log_paths: dict[str, str] | None = None,
    ) -> None:
        if self.emit is None:
            return
        paths = log_paths or {}
        self.emit(
            "diagnostic",
            {
                "phase": "playwright_cli",
                "command": command,
                "exit_code": exit_code,
                "stdout_chars": stdout_chars,
                "stderr_chars": stderr_chars,
                "status": status,
                "stdout_path": paths.get("stdout"),
                "stderr_path": paths.get("stderr"),
            },
        )

    def _ensure_started(self) -> None:
        if not self._running:
            raise BrowserError(
                "browser_not_started", "The Playwright CLI session is not open"
            )

    @property
    def _cdp_endpoint(self) -> str | None:
        value = self.settings.browser_cdp_endpoint
        if value is None:
            value = os.getenv("AGENT_BROWSER_CDP_ENDPOINT")
        return str(value).strip() if value else None


def _decode_json(text: str, command: str) -> Any:
    stripped = text.strip()
    if not stripped:
        if command == "help":
            return {"result": ""}
        raise BrowserError(
            "cli_parse_error",
            "The Playwright CLI returned no JSON output",
            uncertain=True,
        )
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        if command == "help":
            return {"result": stripped[:_MAX_OUTPUT_CHARS]}
        raise BrowserError(
            "cli_parse_error",
            "The Playwright CLI returned invalid JSON",
            uncertain=True,
        ) from None


def _safe_subprocess_env() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(part in key.upper() for part in _SENSITIVE_ENV_PARTS)
    }
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("AGENT_BROWSER_CDP_ENDPOINT", None)
    return environment


def _safe_text(value: Any, limit: int = 1_000) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split())[:limit]


def _with_snapshot_scope(
    command: str,
    args: list[str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    if command != "snapshot":
        return payload
    target = snapshot_scope_target(args)
    if target is None:
        return payload
    scoped = payload.copy()
    scoped["scope_hint"] = {
        "target": target,
        "warning": (
            "This targeted snapshot replaces the active reference scope. "
            "Use a full snapshot or find before using refs outside this target."
        ),
    }
    return scoped
