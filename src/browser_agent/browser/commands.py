"""Allowlisted Playwright CLI commands and literal argument construction."""

from __future__ import annotations

import re
from pathlib import Path

from .errors import BrowserError

COMMANDS = frozenset(
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
        "eval",
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

# Process controls, storage, cookies, routes, and tracing are intentionally absent.
READ_ONLY_COMMANDS = frozenset(
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
ACTION_COMMANDS = frozenset(COMMANDS - READ_ONLY_COMMANDS)

_HOST_FLAGS = frozenset(
    {
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
    }
)
_COMMAND_FLAGS = {
    "attach": frozenset({"--cdp", "--extension"}),
    "open": frozenset({"--browser"}),
    "fill": frozenset({"--submit"}),
    "find": frozenset({"--regex"}),
    "snapshot": frozenset({"--depth", "--filename"}),
    "screenshot": frozenset({"--hires"}),
}
_FLAGS_REQUIRING_VALUE = frozenset(
    {"--browser", "--cdp", "--depth", "--filename", "--regex"}
)
_NEGATIVE_NUMBER = re.compile(r"-\d+(?:\.\d+)?\Z")


def is_read_only(command: str) -> bool:
    """Return whether a supported command is guaranteed to avoid page changes."""

    return isinstance(command, str) and command in READ_ONLY_COMMANDS


def validate_invocation(command: object, raw_args: object) -> tuple[str, list[str]]:
    """Validate and copy the model-controlled portion of one CLI invocation."""

    if not isinstance(command, str) or command not in COMMANDS:
        raise BrowserError(
            "unsupported_command", "That Playwright CLI command is not available"
        )
    if not isinstance(raw_args, list) or not all(
        isinstance(argument, str) for argument in raw_args
    ):
        raise BrowserError(
            "invalid_arguments", "Playwright CLI args must be a list of strings"
        )
    if len(raw_args) > 32 or any(
        len(argument) > 8_000 or "\x00" in argument for argument in raw_args
    ):
        raise BrowserError("invalid_arguments", "Playwright CLI args are too long")

    args = raw_args.copy()
    _validate_flags(command, args)
    return command, args


def _validate_flags(command: str, args: list[str]) -> None:
    allowed_flags = _COMMAND_FLAGS.get(command, frozenset())
    for index, argument in enumerate(args):
        if not argument.startswith("-") or _NEGATIVE_NUMBER.fullmatch(argument):
            continue

        flag, separator, inline_value = argument.partition("=")
        if flag in _HOST_FLAGS:
            raise BrowserError(
                "forbidden_argument",
                "Host and session flags are controlled by the browser agent",
            )
        if flag not in allowed_flags:
            raise BrowserError(
                "forbidden_argument",
                f"The {command} command does not accept that flag",
            )
        if flag not in _FLAGS_REQUIRING_VALUE:
            continue
        has_value = bool(inline_value) if separator else index + 1 < len(args)
        if not has_value:
            raise BrowserError("invalid_arguments", f"The {flag} flag requires a value")


def build_argv(
    executable: str,
    package: str,
    session: str,
    command: str,
    args: list[str],
) -> list[str]:
    """Build the exact subprocess argv; no value is interpreted by a shell."""

    argv = [executable]
    if Path(executable).name == "npx":
        argv.extend(("--yes", package))
    argv.append(f"-s={session}")
    if command == "help":
        argv.append("--help")
    else:
        argv.append(command)
    argv.extend(args)
    argv.append("--json")
    return argv


def with_open_defaults(
    args: list[str],
    *,
    profile: Path,
    headed: bool,
    browser_channel: str | None,
) -> list[str]:
    """Add host-owned persistence and display settings to an open command."""

    rewritten = args.copy()
    _append_flag(rewritten, "--persistent")
    _append_flag(rewritten, "--profile", str(profile))
    if headed:
        _append_flag(rewritten, "--headed")
    if browser_channel:
        _append_flag(rewritten, "--browser", browser_channel)
    return rewritten


def with_attach_defaults(args: list[str], cdp_endpoint: str | None) -> list[str]:
    """Add the configured CDP endpoint when the model did not supply one."""

    rewritten = args.copy()
    if cdp_endpoint:
        _append_flag(rewritten, "--cdp", cdp_endpoint)
    return rewritten


def snapshot_scope_target(args: list[str]) -> str | None:
    """Return the first positional target of a scoped snapshot command."""

    skip_value = False
    for argument in args:
        if skip_value:
            skip_value = False
            continue
        flag, separator, _value = argument.partition("=")
        if flag in {"--depth", "--filename"}:
            skip_value = not separator
            continue
        if not argument.startswith("--"):
            return argument
    return None


def _append_flag(args: list[str], flag: str, value: str | None = None) -> None:
    if _has_flag(args, flag):
        return
    args.append(flag if value is None else f"{flag}={value}")


def _has_flag(args: list[str], flag: str) -> bool:
    return any(argument == flag or argument.startswith(f"{flag}=") for argument in args)
