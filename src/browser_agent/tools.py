"""Strict argument schemas and descriptions for the actor and reviewer tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Playwright(Strict):
    command: str = Field(
        min_length=1,
        max_length=160,
        description="One supported Playwright CLI command, such as open, find, eval, fill, click, snapshot, or screenshot.",
    )
    args: list[str] = Field(
        max_length=32,
        description="The literal arguments and supported flags for that command; do not write a shell command or flags that run code.",
    )


class ReadBrowserArtifact(Strict):
    path: str = Field(min_length=1, max_length=4000)
    offset: int = Field(ge=0, le=1_000_000)


class SearchBrowserArtifact(Strict):
    path: str = Field(min_length=1, max_length=4000)
    query: str = Field(min_length=1, max_length=500)


class AskUser(Strict):
    question: str = Field(min_length=1, max_length=1500)
    kind: Literal["clarification", "login", "challenge"]


class Finish(Strict):
    status: Literal["completed", "partial", "failed"]
    summary: str = Field(min_length=1, max_length=4000)
    remaining: list[str] = Field(max_length=20)


class SecurityReview(Strict):
    """The private reviewer answers only whether approval is needed."""

    needs_approval: bool


SECURITY_REGISTRY = {
    "security_review": (
        SecurityReview,
        "Classify the immediate effect of the proposed Playwright command. Return only needs_approval.",
    )
}


REGISTRY = {
    "playwright": (
        Playwright,
        "Run exactly one supported Playwright CLI command with literal arguments. The host handles safety approval before any consequential effect.",
    ),
    "read_browser_artifact": (
        ReadBrowserArtifact,
        "Read one bounded excerpt from a snapshot, text, or image artifact produced by Playwright CLI. Use this only when a command returned an artifact path; continue from next_offset when needed.",
    ),
    "search_browser_artifact": (
        SearchBrowserArtifact,
        "Search one returned text or snapshot artifact case-insensitively without browser I/O; returns at most 10 bounded excerpts and 6000 characters.",
    ),
    "ask_user": (
        AskUser,
        "Ask only for missing information or manual login/security help that blocks the task. Approval is handled inside the intended browser action.",
    ),
    "finish": (
        Finish,
        "Report the observed outcome against the user's task and stopping boundary.",
    ),
}


type ToolRegistry = dict[str, tuple[type[BaseModel], str]]


def tool_specs(registry: ToolRegistry | None = None) -> list[dict[str, Any]]:
    """Return Responses function definitions for the actor or reviewer."""

    selected = REGISTRY if registry is None else registry
    return [
        {
            "type": "function",
            "name": name,
            "description": description,
            "strict": True,
            "parameters": schema.model_json_schema(),
        }
        for name, (schema, description) in selected.items()
    ]
