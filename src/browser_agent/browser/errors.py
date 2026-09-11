"""Typed errors returned across the browser tool boundary."""

from __future__ import annotations


class BrowserError(Exception):
    """A browser failure whose message is safe to return to the actor."""

    def __init__(self, code: str, message: str, uncertain: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.uncertain = uncertain

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "message": self.message,
            "uncertain": self.uncertain,
        }
