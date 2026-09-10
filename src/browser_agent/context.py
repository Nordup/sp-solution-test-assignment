"""Bounded actor input: original task, notebook, three recent tool exchanges, current page."""

import json

from .prompts import ACTOR
from .tools import tool_specs


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
    messages.extend(history[-6:])
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
