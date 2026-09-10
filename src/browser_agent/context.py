"""Bounded actor input: original task, notebook, recent tool exchanges, current page."""

import json

from .prompts import ACTOR
from .tools import tool_specs

HISTORY_MESSAGES = 20  # Ten exchanges; the notebook carries longer-lived facts.


class ContextOverflow(RuntimeError):
    pass


def build_request(task, observation, notebook, history, image=None, feedback=""):
    current = {
        key: observation.get(key)
        for key in (
            "id",
            "url",
            "title",
            "text",
            "truncated",
            "offset",
            "next_offset",
            "tabs",
            "workspace",
            "active_browser",
            "no_browser",
        )
    }
    messages = [
        {
            "role": "user",
            "content": "Task:\n"
            + task
            + "\n\nNotebook (your notes, not independent proof):\n"
            + notebook,
        }
    ]
    messages.extend(history[-HISTORY_MESSAGES:])
    content = [
        {
            "type": "input_text",
            "text": "Current browser observation:\n"
            + json.dumps(current, ensure_ascii=False)
            + "\nRuntime feedback:\n"
            + feedback,
        }
    ]
    if image:
        content.append({"type": "input_image", "image_url": image, "detail": "auto"})
    messages.append({"role": "user", "content": content})
    return {
        "instructions": ACTOR,
        "input": messages,
        "tools": tool_specs(),
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "truncation": "disabled",
    }
