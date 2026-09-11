"""Errors returned across the browser tool boundary."""

from __future__ import annotations

from typing import Any


class BrowserError(Exception):
    """A browser failure safe to expose to the model."""

    def __init__(self, code: str, message: str, uncertain: bool = False) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.uncertain = bool(uncertain)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "uncertain": self.uncertain,
        }
