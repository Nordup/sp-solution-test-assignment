"""Model request and budget errors exposed to the agent graph."""


class ProviderFailure(RuntimeError):
    """The model provider could not return a trustworthy response."""


class BudgetExceeded(RuntimeError):
    """A model request would exceed the per-task spending limit."""
