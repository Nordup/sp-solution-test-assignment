"""No paid API: durable admission, actual usage and aggregate case holds."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from browser_agent.storage import AdmissionError, BudgetExceeded, Store


def test_p05_next_reservation_refused_before_dispatch(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", 100)
    store.reserve("run", "actor", 90)
    dispatched = []
    with pytest.raises(BudgetExceeded):
        store.reserve("run", "reviewer", 11)
        dispatched.append("reviewer")
    assert not dispatched
    assert store.budget("run")["remaining"] == 10


def test_p06_helpers_retries_judge_share_persistent_cap(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", 100)
    for stage in ("actor", "reviewer", "retry", "judge"):
        store.reserve("run", stage, 25)
        store.settle(stage, 20)
    assert store.budget("run")["settled"] == 80
    with pytest.raises(BudgetExceeded):
        store.reserve("run", "more", 21)


def test_p07_timeout_restart_checkpoint_cannot_refund(tmp_path):
    path = tmp_path / "ledger.db"
    store = Store(path)
    store.create_budget("run", 100)
    store.reserve("run", "timed-out", 75)
    store.mark_unknown("timed-out")
    restarted = Store(path)
    restarted.create_budget("run", 100)
    assert restarted.budget("run")["unknown"] == 75
    with pytest.raises(BudgetExceeded):
        restarted.reserve("run", "retry", 26)
    with pytest.raises(AdmissionError):
        restarted.reserve("run", "timed-out", 1)
    with pytest.raises(AdmissionError):
        restarted.create_budget("run", 101)


def test_aggregate_admission_is_atomic_and_survives_case_crash(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("release", 100, "release")
    store.create_budget("experiment", 70, "experiment")
    store.reserve_case("run", 60, ["release", "experiment"])
    store.reserve("run", "actor", 30)
    store.settle("actor", 12)
    assert store.budget("release")["reserved"] == 60
    with pytest.raises(BudgetExceeded):
        store.reserve_case("run2", 20, ["release", "experiment"])
    assert store.budget("release")["reserved"] == 60
    store.finish_case("run")
    assert store.budget("release")["settled"] == 12
    assert store.budget("experiment")["remaining"] == 58


def test_concurrent_requests_cannot_overspend(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", 100)

    def attempt(index):
        try:
            store.reserve("run", str(index), 60)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(results) == 1
    assert store.budget("run")["reserved"] == 60


def test_budget_disk_failure_prevents_dispatch(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", 100)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER fail_charge BEFORE INSERT ON charges BEGIN SELECT RAISE(FAIL, 'disk failure'); END"
        )
    dispatched = []
    with pytest.raises(sqlite3.DatabaseError):
        store.reserve("run", "actor", 50)
        dispatched.append(True)
    assert not dispatched
    assert store.budget("run")["remaining"] == 100


def test_actual_usage_is_immutable_and_overrun_remains_visible(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", 100)
    store.reserve("run", "actor", 50)
    with pytest.raises(AdmissionError, match="exceeded"):
        store.settle("actor", 101)
    assert store.budget("run")["settled"] == 101
    with pytest.raises(BudgetExceeded):
        store.reserve("run", "next", 1)
    with pytest.raises(AdmissionError):
        store.settle("actor", 0)


@pytest.mark.parametrize("amount", [-1, True, 2.3])
def test_non_integer_or_negative_currency_rejected(tmp_path, amount):
    with pytest.raises(ValueError):
        Store(tmp_path / "ledger.db").create_budget("run", amount)


def test_user_cap_cannot_be_raised_by_configuration(tmp_path):
    with pytest.raises(ValueError, match="cap"):
        Store(tmp_path / "ledger.db").create_budget("run", 5_000_001)
