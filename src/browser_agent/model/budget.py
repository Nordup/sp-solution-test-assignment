"""Conservative, per-task model spending ledger."""

import math
from dataclasses import dataclass

from .errors import BudgetExceeded

MICRO_USD_PER_USD = 1_000_000
MAX_TASK_BUDGET_USD = 5


@dataclass(frozen=True, slots=True)
class Reservation:
    """A charge held before one provider generation attempt."""

    microusd: int


class Budget:
    """Charge attempts up front and settle attempts with reported usage."""

    def __init__(self, cap_usd: float) -> None:
        if not 0 < cap_usd <= MAX_TASK_BUDGET_USD:
            raise ValueError("Per-task budget must be positive and at most $5.")
        self._cap_microusd = math.floor(cap_usd * MICRO_USD_PER_USD)
        self._spent_microusd = 0

    @property
    def cost_usd(self) -> float:
        return self._spent_microusd / MICRO_USD_PER_USD

    def reserve(self, estimated_microusd: int) -> Reservation:
        """Charge an estimate, rejecting the attempt before it is dispatched."""

        self._validate_charge(estimated_microusd)
        if self._spent_microusd + estimated_microusd > self._cap_microusd:
            raise BudgetExceeded(
                "Task spending cap reached; no further model request sent."
            )
        self._spent_microusd += estimated_microusd
        return Reservation(estimated_microusd)

    def projected_cost_usd(
        self, reservation: Reservation, actual_microusd: int
    ) -> float:
        """Return the post-settlement total without mutating the ledger."""

        self._validate_charge(actual_microusd)
        settled = self._spent_microusd - reservation.microusd + actual_microusd
        return settled / MICRO_USD_PER_USD

    def reconcile(self, reservation: Reservation, actual_microusd: int) -> None:
        """Replace a conservative reservation with known provider usage."""

        self._validate_charge(actual_microusd)
        self._spent_microusd += actual_microusd - reservation.microusd
        if self._spent_microusd > self._cap_microusd:
            raise BudgetExceeded("Provider usage exceeded its reserved bound; stopped.")

    @staticmethod
    def _validate_charge(microusd: int) -> None:
        if type(microusd) is not int or microusd < 0:
            raise ValueError("Model charges must be non-negative integer microdollars.")
