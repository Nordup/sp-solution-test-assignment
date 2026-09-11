"""Command policy and argument construction for the Playwright CLI."""

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


def is_read_only(command: str) -> bool:
    """Return whether a supported command only inspects browser state."""

    return isinstance(command, str) and command in READ_ONLY_COMMANDS


def validate_invocation(command: object, raw_args: object) -> tuple[str, list[str]]:
    """Validate the model-controlled portion of one CLI invocation."""

    if not isinstance(command, str) or command not in COMMANDS:
        raise BrowserError(
            "unsupported_command", "That Playwright CLI command is not available"
        )
    if not isinstance(raw_args, list) or any(
        not isinstance(value, str) for value in raw_args
    ):
        raise BrowserError(
            "invalid_arguments", "Playwright CLI args must be a list of strings"
        )
    if len(raw_args) > 32 or any(
        len(value) > 8000 or "\x00" in value for value in raw_args
    ):
        raise BrowserError("invalid_arguments", "Playwright CLI args are too long")
    args = list(raw_args)
    _validate_flags(command, args)
    return command, args


def _validate_flags(command: str, args: list[str]) -> None:
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
            raise BrowserError(
                "forbidden_argument",
                "Host and session flags are controlled by the browser agent",
            )
        if value not in allowed and not any(
            value.startswith(flag + "=") for flag in allowed
        ):
            raise BrowserError(
                "forbidden_argument",
                f"The {command} command does not accept that flag",
            )
        if value in {"--regex", "--depth"} and index + 1 >= len(args):
            raise BrowserError(
                "invalid_arguments", f"The {value} flag requires a value"
            )


def build_argv(
    executable: str,
    package: str,
    session: str,
    command: str,
    args: list[str],
) -> list[str]:
    """Build a literal subprocess argument array without invoking a shell."""

    argv = (
        [executable, "--yes", package]
        if Path(executable).name == "npx"
        else [executable]
    )
    argv.append(f"-s={session}")
    if command == "help":
        argv.append("--help")
        argv.extend(args)
    else:
        argv.extend((command, *args))
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

    rewritten = list(args)
    if not _has_flag(rewritten, "--persistent"):
        rewritten.append("--persistent")
    if not _has_flag(rewritten, "--profile"):
        rewritten.append(f"--profile={profile}")
    if headed and not _has_flag(rewritten, "--headed"):
        rewritten.append("--headed")
    if browser_channel and not _has_flag(rewritten, "--browser"):
        rewritten.append(f"--browser={browser_channel}")
    return rewritten


def with_attach_defaults(args: list[str], cdp_endpoint: str | None) -> list[str]:
    """Add the configured CDP endpoint when the model did not supply one."""

    rewritten = list(args)
    if cdp_endpoint and not _has_flag(rewritten, "--cdp"):
        rewritten.append(f"--cdp={cdp_endpoint}")
    return rewritten


def snapshot_scope_target(args: list[str]) -> str | None:
    """Return the first positional target of a scoped snapshot command."""

    skip_next = False
    for value in args:
        if skip_next:
            skip_next = False
            continue
        if value in {"--depth", "--filename"}:
            skip_next = True
            continue
        if not value.startswith("--"):
            return value
    return None


def _has_flag(args: list[str], flag: str) -> bool:
    return any(value == flag or value.startswith(flag + "=") for value in args)
