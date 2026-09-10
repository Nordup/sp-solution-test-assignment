"""Small in-memory per-run spending cap; no persistence or resume machinery."""

import math


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, cap_usd):
        if not 0 < cap_usd <= 5:
            raise ValueError("Per-task budget must be positive and at most $5.")
        self.cap = math.floor(cap_usd * 1_000_000)
        self.spent = 0

    @property
    def cost_usd(self):
        return self.spent / 1_000_000

    def reserve(self, estimated_microusd):
        if self.spent + estimated_microusd > self.cap:
            raise BudgetExceeded(
                "Task spending cap reached; no further model request sent."
            )
        self.spent += estimated_microusd

    def reconcile(self, reservation, actual_microusd):
        self.spent += actual_microusd - reservation
        if self.spent > self.cap:
            raise BudgetExceeded("Provider usage exceeded its reserved bound; stopped.")
