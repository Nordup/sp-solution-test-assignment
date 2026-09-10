"""Request construction for the single native Playwright actor."""

from __future__ import annotations

from .prompts import ACTOR
from .tools import tool_specs


class ContextOverflow(RuntimeError):
    pass


def build_request(
    task,
    evidence="",
    history=None,
    image=None,
    feedback="",
    *,
    instructions="",
    tools=None,
    compaction=None,
    compact_threshold=150000,
):
    """Build a pinned task request without synthetic browser observations.

    The transport's evidence is intentionally kept out of actor input.  It is
    supplied to the private reviewer only; the actor sees page output through
    native tool results and explicit artifact reads.  A CLI image is passed only when it was explicitly
    returned by a requested command or artifact read.
    """

    history = list(history or [])
    messages = []
    if compaction is not None:
        messages.append(compaction)
    messages.append(
        {
            "role": "user",
            "content": (
                "Task (pinned for this run):\n"
                + str(task)
            ),
        }
    )
    messages.extend(history)
    runtime = str(feedback or "").strip()
    if runtime:
        messages.append({"role": "user", "content": "Host feedback:\n" + runtime})
    if image:
        content = image if isinstance(image, list) else [{"type": "input_image", "image_url": image}]
        messages.append({"role": "user", "content": content})
    return {
        "instructions": ACTOR + ("\n\n" + instructions if instructions else ""),
        "input": messages,
        "tools": list(tools) if tools is not None else tool_specs(),
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "truncation": "disabled",
        "context_management": [
            {"type": "compaction", "compact_threshold": compact_threshold}
        ],
    }
