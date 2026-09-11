"""Async terminal input with task-scoped double-Escape cancellation."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Coroutine, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.input import Input, create_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import Output

DOUBLE_ESCAPE_SECONDS = 0.5
ESCAPE_FLUSH_SECONDS = 0.05


@dataclass
class _EscapeDetector:
    """Recognize two adjacent bare Escape key presses within one task."""

    previous_at: float | None = None

    def accept_escape(self) -> bool:
        now = time.monotonic()
        if (
            self.previous_at is not None
            and now - self.previous_at <= DOUBLE_ESCAPE_SECONDS
        ):
            self.previous_at = None
            return True
        self.previous_at = now
        return False

    def accept(self, key: KeyPress) -> bool:
        if key.key != Keys.Escape:
            self.previous_at = None
            return False
        return self.accept_escape()

    def reset(self) -> None:
        self.previous_at = None


class Terminal:
    """Own terminal input and cancel only the currently running agent task."""

    def __init__(
        self, *, input_stream: Input | None = None, output: Output | None = None
    ) -> None:
        self._owns_input = input_stream is None and sys.stdin.isatty()
        self.input = create_input() if self._owns_input else input_stream
        self._active_task: asyncio.Task[Any] | None = None
        self._session_task: asyncio.Task[Any] | None = None
        self._task_cancel_requested = False
        self._reading = False
        self._closed = False
        self._escapes = _EscapeDetector()
        self._flush_timer: asyncio.TimerHandle | None = None
        self._prompt = self._make_prompt(output) if self.input is not None else None

    def _make_prompt(self, output: Output | None) -> PromptSession[str]:
        bindings = KeyBindings()

        @bindings.add("escape", eager=True)
        def handle_escape(event: Any) -> None:
            queued = event.key_processor.input_queue
            if queued and queued[0].key != Keys.Escape:
                # VT input represents Alt-key chords as Escape plus another key.
                # The prefix is not a bare Escape and cannot cancel the task.
                self._escapes.reset()
                return
            previous = event.previous_key_sequence
            previous_was_escape = len(previous) == 1 and previous[0].key == Keys.Escape
            if not previous_was_escape:
                self._escapes.reset()
            if self._escapes.accept_escape():
                self._cancel_active_task()

        @bindings.add("c-c", eager=True)
        def stop_session(event: Any) -> None:
            if not self._cancel_session():
                event.app.exit(exception=asyncio.CancelledError())

        prompt: PromptSession[str] = PromptSession(
            input=self.input,
            output=output,
            key_bindings=bindings,
        )
        prompt.app.timeoutlen = DOUBLE_ESCAPE_SECONDS
        prompt.app.ttimeoutlen = ESCAPE_FLUSH_SECONDS
        return prompt

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Terminal is closed.")

    async def read(self, message: str, *, erase_when_done: bool = False) -> str:
        """Read one line, giving the prompt exclusive ownership of terminal input."""

        self._ensure_open()
        prompt = self._prompt
        input_stream = self.input
        if self._reading:
            raise RuntimeError("Terminal input already has an active reader.")

        self._reading = True
        try:
            if prompt is None or input_stream is None:
                return await asyncio.to_thread(input, message)
            self._reset_keys()
            previous_erase = bool(getattr(prompt.app, "erase_when_done", False))
            prompt.app.erase_when_done = bool(erase_when_done)
            # The task listener normally owns stdin. It must detach while a human
            # prompt handles editing and its own key bindings.
            try:
                with input_stream.detach():
                    return await prompt.prompt_async(message)
            finally:
                prompt.app.erase_when_done = previous_erase
                self._reset_keys()
        finally:
            self._reading = False

    async def run(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        """Run one task with Escape monitoring and wait for cancellation cleanup."""

        self._ensure_open()
        if self._active_task is not None:
            coroutine.close()
            raise RuntimeError("Terminal already has an active task.")

        session_task = asyncio.current_task()
        active_task = asyncio.create_task(coroutine)
        self._session_task = session_task
        self._active_task = active_task
        self._task_cancel_requested = False
        try:
            with self._listen_for_task_shortcuts():
                try:
                    return await active_task
                except asyncio.CancelledError:
                    # Double Escape cancels the child only. Ctrl+C and external
                    # cancellation mark the session task and must keep propagating.
                    if not self._task_cancel_requested or (
                        session_task is not None and session_task.cancelling()
                    ):
                        raise
                    return None
        finally:
            if not active_task.done():
                active_task.cancel()
            await asyncio.gather(active_task, return_exceptions=True)
            self._active_task = None
            self._session_task = None
            self._task_cancel_requested = False

    @contextmanager
    def _listen_for_task_shortcuts(self) -> Iterator[None]:
        self._reset_keys()
        input_stream = self.input
        try:
            if input_stream is None:
                yield
            else:
                with input_stream.raw_mode(), input_stream.attach(self._read_keys):
                    yield
        finally:
            self._reset_keys()

    def _cancel_active_task(self) -> bool:
        task = self._active_task
        if task is None or task.done() or task.cancelling():
            return False
        self._task_cancel_requested = True
        task.cancel()
        return True

    def _cancel_session(self) -> bool:
        task = self._session_task
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def _read_keys(self) -> None:
        input_stream = self.input
        if input_stream is None:
            return
        self._handle_keys(input_stream.read_keys())
        if input_stream.closed:
            self._cancel_session()
            return
        self._cancel_flush_timer()
        # Escape begins VT sequences such as arrow and function keys. Let the
        # parser collect their remaining bytes before resolving a bare Escape.
        self._flush_timer = asyncio.get_running_loop().call_later(
            ESCAPE_FLUSH_SECONDS,
            self._flush_keys,
        )

    def _flush_keys(self) -> None:
        self._flush_timer = None
        input_stream = self.input
        if input_stream is not None and not input_stream.closed:
            self._handle_keys(input_stream.flush_keys())

    def _handle_keys(self, keys: Sequence[KeyPress]) -> None:
        for key in keys:
            if key.key == Keys.ControlC:
                self._cancel_session()
                self._escapes.reset()
            elif self._escapes.accept(key):
                self._cancel_active_task()

    def _cancel_flush_timer(self) -> None:
        timer, self._flush_timer = self._flush_timer, None
        if timer is not None:
            timer.cancel()

    def _reset_keys(self) -> None:
        self._escapes.reset()
        self._cancel_flush_timer()
        input_stream = self.input
        if input_stream is not None and not input_stream.closed:
            input_stream.flush_keys()

    def close(self) -> None:
        """Release an input stream created by this terminal; safe to call twice."""

        if self._closed:
            return
        self._closed = True
        self._reset_keys()
        input_stream = self.input
        if self._owns_input and input_stream is not None:
            input_stream.close()
