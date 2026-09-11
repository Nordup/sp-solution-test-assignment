"""Lifecycle and subprocess transport for one Playwright CLI session."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time
from collections.abc import Callable
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


class PlaywrightCLI:
    """One persistent browser session backed by the official Playwright CLI."""

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
        self.profile = (
            settings.browser_profile_dir or root / "profiles" / "default"
        ).resolve()
        self._artifacts = ArtifactStore(
            session_root=root / "browser" / self.session,
            evidence_root=root / "evidence",
        )
        self._evidence = EvidenceCache(self._artifacts)
        self._started = False
        self._closed = True
        self._browser_open = False
        self._attached = False
        self._lock = asyncio.Lock()
        self.ownership = "attached" if self._cdp_endpoint else "owned"

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
        return self._started and not self._closed

    @property
    def attached(self) -> bool:
        return self._attached

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
        """Prepare private storage without launching or attaching a browser."""

        async with self._lock:
            if self._started and not self._closed:
                raise BrowserError(
                    "already_started",
                    "The Playwright CLI session is already running",
                )
            self.settings.prepare()
            self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.profile.chmod(0o700)
            self._artifacts.prepare_session()
            self._started = True
            self._closed = False
            self._browser_open = False

    async def close(self) -> None:
        """Best-effort teardown of only the browser session this object owns."""

        async with self._lock:
            if not self._started or self._closed:
                return
            if self._browser_open:
                command = "detach" if self._attached else "close"
                try:
                    await self._invoke_cli(command, [])
                except BrowserError:
                    pass
            self._closed = True
            self._started = False
            self._browser_open = False

    async def prepare_task(self) -> None:
        """Clear prior task evidence without reading or navigating the browser."""

        self._evidence.clear()

    async def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute one model-facing browser tool call."""

        command: str | None = None
        try:
            self._ensure_started()
            if not isinstance(args, dict):
                raise BrowserError(
                    "invalid_arguments",
                    "Browser command arguments must be an object",
                )
            if tool == "playwright":
                command, cli_args = validate_invocation(
                    args.get("command"),
                    args.get("args", []),
                )
                cli_args, screenshot_path = self._prepare_command(command, cli_args)
                async with self._lock:
                    if command in {"open", "attach"}:
                        self._browser_open = True
                        self._attached = command == "attach"
                    dispatched_at_ns = time.time_ns()
                    payload = await self._invoke_cli(command, cli_args)
                    self._evidence.update(
                        command,
                        payload,
                        fresh_after_ns=dispatched_at_ns,
                    )
                    actor_payload = self._artifacts.compact_for_actor(command, payload)
                    actor_payload = _with_snapshot_scope(
                        command, cli_args, actor_payload
                    )
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
                if screenshot_path is not None:
                    result["content"] = self._artifacts.screenshot_content(
                        screenshot_path
                    )
                return result

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
            return {
                "status": "error",
                "tool": tool,
                **({"command": command} if command else {}),
                "error": exc.as_dict(),
            }

    def _prepare_command(
        self,
        command: str,
        args: list[str],
    ) -> tuple[list[str], Path | None]:
        screenshot_path: Path | None = None
        if command == "open":
            args = with_open_defaults(
                args,
                profile=self.profile,
                headed=self.settings.browser_headed,
                browser_channel=self.settings.browser_channel,
            )
        elif command == "attach":
            args = with_attach_defaults(args, self._cdp_endpoint)
        elif command == "snapshot":
            args = self._artifacts.rewrite_snapshot_args(args)
        elif command == "screenshot":
            args, screenshot_path = self._artifacts.prepare_screenshot(args)
        return args, screenshot_path

    async def _invoke_cli(self, command: str, args: list[str]) -> dict[str, Any]:
        """Run one literal CLI subprocess and decode its JSON envelope."""

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
                "cli_unavailable",
                "Could not start the Playwright CLI",
            ) from exc

        communication = asyncio.create_task(process.communicate())
        try:
            stdout, stderr = await asyncio.shield(communication)
        except asyncio.CancelledError:
            stdout, stderr = await self._reap_cancelled_process(process, communication)
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

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        log_paths = self._log_process_output(command, stdout, stderr)
        try:
            parsed = _decode_json(stdout_text, command)
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
        nested_result = parsed.get("result")
        nested_error = (
            isinstance(nested_result, dict) and nested_result.get("isError") is True
        )
        cli_error = parsed.get("isError") is True or nested_error
        self._emit_diagnostic(
            command,
            process.returncode,
            len(stdout_text),
            len(stderr_text),
            "error" if cli_error else "ok",
            log_paths,
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

        stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", command)[:40] + "-" + uuid4().hex[:8]
        paths: dict[str, str] = {}
        for suffix, data in (("stdout", stdout), ("stderr", stderr)):
            path = self.browser_session_dir / f"{stem}-{suffix}.log"
            try:
                path.write_bytes(data[-_MAX_PRIVATE_LOG_BYTES:])
                path.chmod(0o600)
                paths[suffix] = str(path)
            except OSError:
                continue
        return paths

    def _emit_diagnostic(
        self,
        command: str,
        exit_code: int | None,
        stdout_chars: int,
        stderr_chars: int,
        status: str,
        log_paths: dict[str, str] | None = None,
    ) -> None:
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

    def _ensure_started(self) -> None:
        if not self.is_open:
            raise BrowserError(
                "browser_not_started",
                "The Playwright CLI session is not open",
            )

    @property
    def _cdp_endpoint(self) -> str | None:
        value = self.settings.browser_cdp_endpoint
        if value is None:
            value = os.getenv("AGENT_BROWSER_CDP_ENDPOINT")
        return str(value).strip() if value else None


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
        if command == "help":
            return {"result": text[:_MAX_OUTPUT_CHARS]}
        raise BrowserError(
            "cli_parse_error",
            "The Playwright CLI returned invalid JSON",
            uncertain=True,
        ) from None


def _safe_subprocess_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(part in key.upper() for part in _SENSITIVE_ENV_PARTS)
    }
    env.pop("OPENAI_API_KEY", None)
    env.pop("AGENT_BROWSER_CDP_ENDPOINT", None)
    return env


def _safe_text(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _with_snapshot_scope(
    command: str,
    args: list[str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    if command != "snapshot":
        return payload
    target = snapshot_scope_target(args)
    if not target:
        return payload
    scoped = dict(payload)
    scoped["scope_hint"] = {
        "target": target,
        "warning": (
            "This targeted snapshot replaces the active reference scope. "
            "Use a full snapshot or find before using refs outside this target."
        ),
    }
    return scoped
