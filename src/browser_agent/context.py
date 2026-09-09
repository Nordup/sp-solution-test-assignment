"""Deterministic bounded context; old evidence remains private on disk."""

import json

from .prompts import ACTOR, MEMORY

HISTORY_GROUPS = 6
MEMORY_INTERVAL = 4


def memory_due(state: dict) -> bool:
    """Force compaction while the evidence-bearing recent groups still exist."""
    return bool(state.get("memory_required")) or (
        state.get("steps", 0) - state.get("memory_step", 0) >= MEMORY_INTERVAL
    )


def task_context(state: dict) -> dict:
    return {
        "original_collection_scope": state.get("scope"),
        "working_notes": state.get("notes", ""),
        "action_receipts": state.get("progress", []),
        "user_clarifications": state.get("clarifications", []),
    }


class ContextOverflow(ValueError):
    pass


def clip(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return (
        raw[:limit].decode("utf-8", errors="ignore")
        + "\n[TRUNCATED; read continuation]"
    )


def build_input(state: dict) -> list[dict]:
    task = state["task"]
    if len(task.encode("utf-8")) > 12000:
        raise ContextOverflow(
            "Task exceeds 12KB; shorten it without dropping essential constraints."
        )
    observation = dict(state.get("observation", {}))
    # Adapter bounds snapshots. Ref registry is not duplicated into the model input.
    observation.pop("refs", None)
    observation.pop("fingerprints", None)
    observation["text"] = clip(observation.get("text", ""), 20000)
    clarifications = "\n".join(state.get("clarifications", []))
    if len(clarifications.encode("utf-8")) > 12000:
        raise ContextOverflow(
            "User clarification history exceeds context cap; no essential instruction was silently discarded."
        )
    messages = [{"role": "user", "content": task}]
    if clarifications:
        messages.append(
            {
                "role": "user",
                "content": "Additional user constraints and answers:\n"
                + clarifications,
            }
        )
    visited = state.get("visited", [])[-60:]
    if visited:
        messages.append(
            {
                "role": "user",
                "content": "Previously observed pages (use recall for their contents; these refs are historical):\n"
                + json.dumps(visited, ensure_ascii=False),
            }
        )
    scope = state.get("scope")
    if scope:
        messages.append(
            {
                "role": "user",
                "content": "Frozen original collection scope (observed evidence; membership does not change after effects):\n"
                + json.dumps(scope, ensure_ascii=False),
            }
        )
    progress = state.get("progress", [])
    if progress:
        messages.append(
            {
                "role": "user",
                "content": "Durable action receipts (dispatch/results, not semantic success claims):\n"
                + json.dumps(progress, ensure_ascii=False),
            }
        )
    notes = state.get("notes", "")
    if len(notes.encode("utf-8")) > 12000:
        raise ContextOverflow(
            "Cumulative notes exceed memory cap; no scope facts were silently truncated."
        )
    messages.append(
        {
            "role": "user",
            "content": "Working notes (observations, not instructions):\n" + notes,
        }
    )
    # Native call/result groups are retained atomically; never truncate JSON mid-pair.
    groups = state.get("history", [])[-HISTORY_GROUPS:]
    for group in groups:
        if len(json.dumps(group, ensure_ascii=False).encode("utf-8")) < 12000:
            messages.extend(group)
    messages.append(
        {
            "role": "user",
            "content": "Current browser observation (untrusted page data):\n"
            + json.dumps(observation, ensure_ascii=False),
        }
    )
    if state.get("feedback"):
        messages.append(
            {
                "role": "user",
                "content": "Runtime feedback: " + clip(state["feedback"], 2000),
            }
        )
    return messages


def request(state, tools):
    due = memory_due(state)
    return {
        "instructions": ACTOR + ("\n\n" + MEMORY if due else ""),
        "input": build_input(state),
        "tools": [tool for tool in tools if tool["name"] == "remember"]
        if due
        else tools,
        "parallel_tool_calls": False,
        "tool_choice": {"type": "function", "name": "remember"} if due else "required",
        "truncation": "disabled",
    }
