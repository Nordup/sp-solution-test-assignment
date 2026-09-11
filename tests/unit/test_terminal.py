"""Exercise real terminal escape parsing without paid model or browser calls."""

import asyncio
import os

import pytest
from prompt_toolkit.input import create_input
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from browser_agent.terminal import Terminal


async def settle():
    # Allow the VT parser to resolve an otherwise ambiguous bare Escape.
    await asyncio.sleep(0.12)


@pytest.mark.asyncio
async def test_double_escape_cancels_once_and_waits_for_cleanup():
    with create_pipe_input() as pipe:
        terminal = Terminal(input_stream=pipe, output=DummyOutput())
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cancellations = []

        async def work():
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancellations.append(True)
                cleaning.set()
                await release.wait()
                raise

        running = asyncio.create_task(terminal.run(work()))
        await started.wait()
        pipe.send_text("\x1b\x1b")
        await asyncio.wait_for(cleaning.wait(), 1)
        pipe.send_text("\x1b\x1b\x1b\x1b")
        await settle()
        assert not running.done()
        release.set()
        assert await asyncio.wait_for(running, 1) is None
        assert cancellations == [True]


@pytest.mark.asyncio
async def test_only_two_consecutive_recent_escapes_stop_work():
    with create_pipe_input() as pipe:
        terminal = Terminal(input_stream=pipe, output=DummyOutput())
        started = asyncio.Event()

        async def work():
            started.set()
            await asyncio.Future()

        running = asyncio.create_task(terminal.run(work()))
        await started.wait()
        pipe.send_text("\x1b")
        await asyncio.sleep(0.65)
        pipe.send_text("\x1b")
        await settle()
        assert not running.done()
        # Arrow/function keys and Alt combinations contain ESC bytes, but must
        # not be interpreted as consecutive Escape key presses.
        for text in ("x", "\x1b[A\x1b[B", "\x1bOP\x1bOQ", "\x1ba\x1bb", "\x1bx\x1b"):
            pipe.send_text(text)
            await settle()
            assert not running.done()
        pipe.send_text("x\x1b")
        await settle()
        assert not running.done()
        pipe.send_text("\x1b")
        assert await asyncio.wait_for(running, 1) is None


@pytest.mark.asyncio
async def test_stop_at_human_prompt_releases_input_for_next_task():
    with create_pipe_input() as pipe:
        terminal = Terminal(input_stream=pipe, output=DummyOutput())
        entered = asyncio.Event()
        approved = []

        async def work():
            entered.set()
            approved.append(await terminal.read("Approve: "))

        running = asyncio.create_task(terminal.run(work()))
        await entered.wait()
        await settle()
        pipe.send_text("ye\x1b\x1b")
        assert await asyncio.wait_for(running, 1) is None
        assert approved == []

        next_prompt = asyncio.create_task(terminal.read("Task: "))
        await settle()
        pipe.send_text("second task\r")
        assert await asyncio.wait_for(next_prompt, 1) == "second task"


@pytest.mark.asyncio
async def test_prompt_erasure_is_opt_in_and_restored_after_read():
    """CLI transcript reads can erase the editable line without changing embeds."""

    with create_pipe_input() as pipe:
        terminal = Terminal(input_stream=pipe, output=DummyOutput())
        seen = []

        async def fake_prompt(_message):
            seen.append(terminal._prompt.app.erase_when_done)
            return "answer"

        terminal._prompt.prompt_async = fake_prompt
        assert await terminal.read("Task: ") == "answer"
        assert await terminal.read("Task: ", erase_when_done=True) == "answer"

        assert seen == [False, True]
        assert terminal._prompt.app.erase_when_done is False


@pytest.mark.asyncio
async def test_human_prompt_timeout_single_escape_and_arrows_do_not_cancel():
    with create_pipe_input() as pipe:
        terminal = Terminal(input_stream=pipe, output=DummyOutput())
        running = asyncio.create_task(terminal.run(terminal.read("Reply: ")))
        await settle()
        pipe.send_text("\x1b")
        await asyncio.sleep(0.7)
        pipe.send_text("\x1b")
        await asyncio.sleep(0.7)
        assert not running.done()
        pipe.send_text("yes\x1b[D\x1b[C\r")
        assert await asyncio.wait_for(running, 1) == "yes"


@pytest.mark.asyncio
async def test_listener_resumes_after_human_answer_and_idle_escape_does_nothing():
    with create_pipe_input() as pipe:
        terminal = Terminal(input_stream=pipe, output=DummyOutput())
        idle = asyncio.create_task(terminal.read("Task: "))
        await settle()
        pipe.send_text("\x1b\x1bfirst\r")
        assert await asyncio.wait_for(idle, 1) == "first"
        answered = asyncio.Event()

        async def work():
            assert await terminal.read("Reply: ") == "continue"
            answered.set()
            await asyncio.Future()

        running = asyncio.create_task(terminal.run(work()))
        await settle()
        pipe.send_text("continue\r")
        await asyncio.wait_for(answered.wait(), 1)
        pipe.send_text("\x1b\x1b")
        assert await asyncio.wait_for(running, 1) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("human_prompt", [False, True])
async def test_ctrl_c_still_exits_session(human_prompt):
    with create_pipe_input() as pipe:
        terminal = Terminal(input_stream=pipe, output=DummyOutput())

        async def work():
            if human_prompt:
                await terminal.read("Reply: ")
            else:
                await asyncio.Future()

        running = asyncio.create_task(terminal.run(work()))
        await settle()
        pipe.send_text("\x03")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 1)


@pytest.mark.asyncio
async def test_external_cancellation_during_stop_cleanup_is_not_swallowed():
    with create_pipe_input() as pipe:
        terminal = Terminal(input_stream=pipe, output=DummyOutput())
        cleaning = asyncio.Event()

        async def work():
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await asyncio.Future()

        running = asyncio.create_task(terminal.run(work()))
        await settle()
        pipe.send_text("\x1b\x1b")
        await asyncio.wait_for(cleaning.wait(), 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 1)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX terminal attributes")
async def test_real_tty_restores_terminal_mode_after_stop():
    import pty
    import termios

    master, slave = pty.openpty()
    try:
        with os.fdopen(slave, "r") as stream:
            initial = termios.tcgetattr(stream)
            terminal = Terminal(
                input_stream=create_input(stdin=stream), output=DummyOutput()
            )
            running = asyncio.create_task(terminal.run(asyncio.sleep(30)))
            await settle()
            assert not termios.tcgetattr(stream)[3] & termios.ICANON
            os.write(master, b"\x1b\x1b")
            assert await asyncio.wait_for(running, 1) is None
            restored = termios.tcgetattr(stream)
            # macOS sets this transient kernel flag when ICANON is restored.
            restored[3] &= ~getattr(termios, "PENDIN", 0)
            initial[3] &= ~getattr(termios, "PENDIN", 0)
            assert restored == initial
    finally:
        os.close(master)
