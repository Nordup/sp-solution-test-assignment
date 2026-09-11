"""Async terminal input and task-scoped double-Escape interruption."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Coroutine, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.input import Input, create_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import Output

DOUBLE_ESCAPE_SECONDS = 0.5
ESCAPE_FLUSH_SECONDS = 0.05


class Terminal:
    """Own terminal input and cancel only the active task on double Escape."""

    def __init__(
        self, *, input_stream: Input | None = None, output: Output | None = None
    ) -> None:
        self.input = input_stream
        self._owns_input = input_stream is None and sys.stdin.isatty()
        if self._owns_input:
            self.input = create_input()
        self._active_task: asyncio.Task[Any] | None = None
        self._session_task: asyncio.Task[Any] | None = None
        self._stop_requested = False
        self._last_escape: float | None = None
        self._flush_timer: asyncio.TimerHandle | None = None
        self._prompt: PromptSession[str] | None = None
        if self.input is not None:
            bindings = KeyBindings()

            @bindings.add("escape")
            def single_escape(_event):
                pass

            @bindings.add("escape", "escape", eager=True)
            def double_escape(_event):
                self._stop()

            @bindings.add("c-c", eager=True)
            def exit_session(event):
                if self._session_task is not None:
                    self._session_task.cancel()
                else:
                    event.app.exit(exception=asyncio.CancelledError())

            self._prompt = PromptSession(
                input=self.input, output=output, key_bindings=bindings
            )
            self._prompt.app.timeoutlen = DOUBLE_ESCAPE_SECONDS
            self._prompt.app.ttimeoutlen = ESCAPE_FLUSH_SECONDS

    async def read(self, message: str, *, erase_when_done: bool = False) -> str:
        """Read a line; optionally erase the TTY prompt before rendering its turn."""

        if self._prompt is None:
            return await asyncio.to_thread(input, message)
        self._reset_keys()
        # Only one reader owns stdin. A cancelled human prompt must unwind before
        # the background listener (or the next Task prompt) takes over.
        previous_erase = getattr(self._prompt.app, "erase_when_done", False)
        self._prompt.app.erase_when_done = bool(erase_when_done)
        with self.input.detach():
            try:
                return await self._prompt.prompt_async(message)
            finally:
                self._prompt.app.erase_when_done = previous_erase
                self._reset_keys()

    async def run(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        """Run work while listening for cancellation; leave the terminal reusable."""
        self._session_task = asyncio.current_task()
        self._stop_requested = False
        self._active_task = asyncio.create_task(coroutine)
        try:
            with self._listen():
                try:
                    return await self._active_task
                except asyncio.CancelledError:
                    # A shortcut stops this run; Ctrl+C/SIGINT still exits the
                    # whole session, even if it arrives during stop cleanup.
                    if not self._stop_requested or self._session_task.cancelling():
                        raise
                    return None
        finally:
            if not self._active_task.done():
                self._active_task.cancel()
                await asyncio.gather(self._active_task, return_exceptions=True)
            self._active_task = None
            self._session_task = None

    @contextmanager
    def _listen(self) -> Iterator[None]:
        self._reset_keys()
        try:
            if self.input is None:
                yield
            else:
                with self.input.raw_mode(), self.input.attach(self._read_keys):
                    yield
        finally:
            self._reset_keys()

    def _stop(self) -> None:
        task = self._active_task
        if task is not None and not task.done() and not task.cancelling():
            self._stop_requested = True
            task.cancel()

    def _read_keys(self) -> None:
        self._handle_keys(self.input.read_keys())
        if self.input.closed:
            self._session_task.cancel()
            return
        if self._flush_timer is not None:
            self._flush_timer.cancel()
        # A bare ESC is also a VT escape-sequence prefix; give arrows/function
        # keys time to arrive before asking the parser to resolve it.
        self._flush_timer = asyncio.get_running_loop().call_later(
            ESCAPE_FLUSH_SECONDS, self._flush_keys
        )

    def _flush_keys(self) -> None:
        self._flush_timer = None
        self._handle_keys(self.input.flush_keys())

    def _handle_keys(self, keys: Sequence[KeyPress]) -> None:
        for key in keys:
            if key.key == Keys.ControlC:
                self._session_task.cancel()
            if key.key != Keys.Escape:
                self._last_escape = None
                continue
            now = time.monotonic()
            if (
                self._last_escape is not None
                and now - self._last_escape <= DOUBLE_ESCAPE_SECONDS
            ):
                self._last_escape = None
                self._stop()
            else:
                self._last_escape = now

    def _reset_keys(self) -> None:
        self._last_escape = None
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None
        if self.input is not None:
            # Do not carry an unresolved ESC prefix into the next prompt/run.
            self.input.flush_keys()

    def close(self) -> None:
        self._reset_keys()
        if self._owns_input:
            self.input.close()
