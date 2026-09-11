"""In-memory spending limit shared by all model calls in one task."""

import math

from .errors import BudgetExceeded

MAX_TASK_BUDGET_USD = 5
MICRO_USD_PER_USD = 1_000_000


class Budget:
    """Reserve and reconcile model costs against a per-task cap."""

    def __init__(self, cap_usd: float) -> None:
        if not 0 < cap_usd <= MAX_TASK_BUDGET_USD:
            raise ValueError("Per-task budget must be positive and at most $5.")
        self.cap = math.floor(cap_usd * MICRO_USD_PER_USD)
        self.spent = 0

    @property
    def cost_usd(self) -> float:
        return self.spent / MICRO_USD_PER_USD

    def reserve(self, estimated_microusd: int) -> None:
        if self.spent + estimated_microusd > self.cap:
            raise BudgetExceeded(
                "Task spending cap reached; no further model request sent."
            )
        self.spent += estimated_microusd

    def reconcile(self, reservation: int, actual_microusd: int) -> None:
        self.spent += actual_microusd - reservation
        if self.spent > self.cap:
            raise BudgetExceeded("Provider usage exceeded its reserved bound; stopped.")
