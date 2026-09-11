"""Model client and failures used by the browser agent."""

from .client import ModelClient
from .errors import BudgetExceeded, ProviderFailure

__all__ = ["BudgetExceeded", "ModelClient", "ProviderFailure"]
