"""Durable admission records, independent of graph checkpoints.

Amounts are integer micro-USD. Every connection uses FULL synchronous writes;
an admission returns only after its transaction has committed. Callers must not
dispatch on any exception from this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class AdmissionError(RuntimeError):
    """No external effect/request is authorized by a failed admission."""


class BudgetExceeded(AdmissionError):
    pass


class ApprovalError(AdmissionError):
    pass


class DuplicateAction(AdmissionError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def effect_identity(effect: dict) -> dict:
    """Semantic identity excludes dispatch syntax; exact payload remains bound separately.

    The adapter supplies operation, destination and objects from resolved browser
    metadata. If these are missing, binding the whole effect is conservative but
    cannot establish equivalence: the policy must require clarification.
    """
    return {
        key: effect[key]
        for key in ("operation", "destination", "objects")
        if key in effect
    }


def _amount(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Amounts must be nonnegative integer micro-USD")
    return value


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        os.chmod(self.path, 0o600)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS budgets (
                    id TEXT PRIMARY KEY, cap INTEGER NOT NULL, kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS closed_budgets (
                    id TEXT PRIMARY KEY REFERENCES budgets(id));
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY, amount INTEGER NOT NULL, actual INTEGER,
                    status TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS charges (
                    reservation_id TEXT NOT NULL, budget_id TEXT NOT NULL,
                    PRIMARY KEY(reservation_id, budget_id),
                    FOREIGN KEY(reservation_id) REFERENCES reservations(id),
                    FOREIGN KEY(budget_id) REFERENCES budgets(id));
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action_id TEXT NOT NULL,
                    action_hash TEXT NOT NULL, effect_hash TEXT NOT NULL,
                    identity_hash TEXT NOT NULL, generation TEXT NOT NULL,
                    details TEXT NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS denials (
                    run_id TEXT NOT NULL, identity_hash TEXT NOT NULL,
                    effect_hash TEXT NOT NULL, details TEXT NOT NULL,
                    PRIMARY KEY(run_id, identity_hash));
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action_hash TEXT NOT NULL,
                    effect_hash TEXT NOT NULL, identity_hash TEXT NOT NULL,
                    generation TEXT NOT NULL, approval_id TEXT, status TEXT NOT NULL,
                    details TEXT NOT NULL, evidence TEXT, created REAL NOT NULL);
            """)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def create_budget(
        self, scope_id: str, cap_microusd: int, kind: str = "task"
    ) -> dict:
        cap = _amount(cap_microusd)
        if kind == "task" and cap > 5_000_000:
            raise ValueError("Logical tasks cannot exceed the user's $5 cap")
        with self._transaction() as db:
            prior = db.execute(
                "SELECT * FROM budgets WHERE id=?", (scope_id,)
            ).fetchone()
            if prior and (prior["cap"] != cap or prior["kind"] != kind):
                raise AdmissionError("Existing budget cannot be reset or resized")
            db.execute(
                "INSERT OR IGNORE INTO budgets VALUES (?,?,?)", (scope_id, cap, kind)
            )
        return self.budget(scope_id)

    @staticmethod
    def _budget(db, scope_id: str) -> dict:
        row = db.execute("SELECT * FROM budgets WHERE id=?", (scope_id,)).fetchone()
        if not row:
            raise AdmissionError(f"Unknown budget: {scope_id}")
        totals = db.execute(
            """SELECT
            COALESCE(SUM(CASE WHEN r.status='settled' THEN r.actual ELSE 0 END),0) settled,
            COALESCE(SUM(CASE WHEN r.status!='settled' THEN r.amount ELSE 0 END),0) reserved,
            COALESCE(SUM(CASE WHEN r.status='unknown' THEN r.amount ELSE 0 END),0) unknown
            FROM charges c JOIN reservations r ON r.id=c.reservation_id WHERE c.budget_id=?""",
            (scope_id,),
        ).fetchone()
        result = dict(row) | dict(totals)
        result["remaining"] = result["cap"] - result["settled"] - result["reserved"]
        result["closed"] = bool(
            db.execute(
                "SELECT 1 FROM closed_budgets WHERE id=?", (scope_id,)
            ).fetchone()
        )
        return result

    def budget(self, scope_id: str) -> dict:
        with self._connection() as db:
            return self._budget(db, scope_id)

    def reserve(
        self,
        task_id: str,
        attempt_id: str,
        amount_microusd: int,
        aggregate_ids: tuple[str, ...] | list[str] = (),
    ) -> str:
        amount = _amount(amount_microusd)
        scopes = {task_id, *aggregate_ids}
        with self._transaction() as db:
            if db.execute(
                "SELECT 1 FROM reservations WHERE id=?", (attempt_id,)
            ).fetchone():
                raise AdmissionError(
                    "Attempt already admitted; never dispatch it twice"
                )
            for scope in scopes:
                budget = self._budget(db, scope)
                if budget["closed"] or budget["remaining"] < amount:
                    raise BudgetExceeded(f"Budget exhausted: {scope}")
            db.execute(
                "INSERT INTO reservations VALUES (?,?,NULL,'reserved',?)",
                (attempt_id, amount, time.time()),
            )
            db.executemany(
                "INSERT INTO charges VALUES (?,?)", [(attempt_id, s) for s in scopes]
            )
        return attempt_id

    def mark_unknown(self, attempt_id: str) -> None:
        with self._transaction() as db:
            row = db.execute(
                "SELECT status FROM reservations WHERE id=?", (attempt_id,)
            ).fetchone()
            if not row or row["status"] == "settled":
                raise AdmissionError("Unknown or already settled attempt")
            db.execute(
                "UPDATE reservations SET status='unknown' WHERE id=?", (attempt_id,)
            )

    def settle(self, attempt_id: str, actual_microusd: int) -> None:
        actual = _amount(actual_microusd)
        exceeded = False
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM reservations WHERE id=?", (attempt_id,)
            ).fetchone()
            if not row:
                raise AdmissionError("Usage without a prior reservation")
            if row["status"] == "settled":
                if row["actual"] != actual:
                    raise AdmissionError("Settled usage is immutable")
                return
            exceeded = actual > row["amount"]
            # Persist even unexpected billed usage. Never hide an overrun by rollback.
            db.execute(
                "UPDATE reservations SET actual=?,status='settled' WHERE id=?",
                (actual, attempt_id),
            )
        if exceeded:
            raise AdmissionError(
                "Provider exceeded reserved usage; stop and review pricing"
            )

    def reserve_case(
        self, task_id: str, cap_microusd: int, aggregate_ids: list[str]
    ) -> str:
        """Hold the complete case cap in each aggregate before launching its actor.

        During that case reserve individual calls against the task only. Complete
        the hold with finish_case; a crash leaves the conservative hold intact.
        """
        if not aggregate_ids:
            raise ValueError("Case admission requires an aggregate budget")
        cap = _amount(cap_microusd)
        if cap > 5_000_000:
            raise ValueError("Logical tasks cannot exceed the user's $5 cap")
        hold = "case:" + task_id
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM budgets WHERE id=?", (task_id,)).fetchone():
                raise AdmissionError(
                    "Case already admitted; resume its existing task ledger"
                )
            for scope in set(aggregate_ids):
                budget = self._budget(db, scope)
                if budget["closed"] or budget["remaining"] < cap:
                    raise BudgetExceeded(f"Budget exhausted: {scope}")
            db.execute("INSERT INTO budgets VALUES (?,?,'task')", (task_id, cap))
            db.execute(
                "INSERT INTO reservations VALUES (?,?,NULL,'reserved',?)",
                (hold, cap, time.time()),
            )
            db.executemany(
                "INSERT INTO charges VALUES (?,?)",
                [(hold, scope) for scope in set(aggregate_ids)],
            )
        return hold

    def finish_case(self, task_id: str) -> None:
        with self._transaction() as db:
            budget = self._budget(db, task_id)
            hold = db.execute(
                "SELECT * FROM reservations WHERE id=?", ("case:" + task_id,)
            ).fetchone()
            if not hold:
                raise AdmissionError("Case was not admitted against an aggregate")
            db.execute("INSERT OR IGNORE INTO closed_budgets VALUES (?)", (task_id,))
            if budget["reserved"]:
                db.execute(
                    "UPDATE reservations SET status='unknown' WHERE id=?", (hold["id"],)
                )
            elif hold["status"] != "settled":
                db.execute(
                    "UPDATE reservations SET status='settled',actual=? WHERE id=?",
                    (budget["settled"], hold["id"]),
                )

    def denied(self, run_id: str, effect: dict) -> bool:
        identity = fingerprint(effect_identity(effect))
        with self._connection() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM denials WHERE run_id=? AND identity_hash=?",
                    (run_id, identity),
                ).fetchone()
            )

    def has_denials(self, run_id: str) -> bool:
        with self._connection() as db:
            return bool(
                db.execute("SELECT 1 FROM denials WHERE run_id=?", (run_id,)).fetchone()
            )

    def request_approval(
        self,
        run_id: str,
        action_id: str,
        action: dict,
        effect: dict,
        generation: str,
        ttl_seconds: int = 300,
    ) -> str:
        if ttl_seconds <= 0:
            raise ValueError("Approval lifetime must be positive")
        request_id = str(uuid.uuid4())
        with self._transaction() as db:
            if self._denied(db, run_id, effect):
                raise ApprovalError(
                    "This effect was denied; explicit new instruction is required"
                )
            db.execute(
                "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,'pending')",
                (
                    request_id,
                    run_id,
                    action_id,
                    fingerprint(action),
                    fingerprint(effect),
                    fingerprint(effect_identity(effect)),
                    str(generation),
                    canonical({"action": action, "effect": effect}),
                    time.time() + ttl_seconds,
                ),
            )
        return request_id

    @staticmethod
    def _denied(db, run_id: str, effect: dict) -> bool:
        return bool(
            db.execute(
                "SELECT 1 FROM denials WHERE run_id=? AND identity_hash=?",
                (run_id, fingerprint(effect_identity(effect))),
            ).fetchone()
        )

    def approval(self, request_id: str) -> dict:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM approvals WHERE id=?", (request_id,)
            ).fetchone()
            if not row:
                raise ApprovalError("Unknown approval")
            return dict(row) | {"details": json.loads(row["details"])}

    def decide_approval(self, request_id: str, approved: bool) -> None:
        if type(approved) is not bool:
            raise ApprovalError("Approval requires an explicit boolean decision")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM approvals WHERE id=?", (request_id,)
            ).fetchone()
            if not row or row["status"] != "pending":
                raise ApprovalError("Approval is no longer pending")
            if time.time() >= row["expires"]:
                raise ApprovalError("Approval expired")
            db.execute(
                "UPDATE approvals SET status=? WHERE id=?",
                ("approved" if approved else "denied", request_id),
            )
            if not approved:
                db.execute(
                    "INSERT OR REPLACE INTO denials VALUES (?,?,?,?)",
                    (
                        row["run_id"],
                        row["identity_hash"],
                        row["effect_hash"],
                        row["details"],
                    ),
                )

    def dispatch(
        self,
        run_id: str,
        action_id: str,
        action: dict,
        effect: dict,
        generation: str,
        approval_id: str | None = None,
        requires_approval: bool = True,
    ) -> None:
        """Atomic write-before-effect admission; caller performs exactly one effect.

        Both `action` and `effect` must be independently re-resolved under the
        browser lock after human approval. Never pass actor-supplied safety flags.
        """
        with self._transaction() as db:
            if self._denied(db, run_id, effect):
                raise ApprovalError("Denied effect cannot dispatch")
            if db.execute("SELECT 1 FROM actions WHERE id=?", (action_id,)).fetchone():
                raise DuplicateAction("Action already dispatched; inspect its outcome")
            if (
                requires_approval
                and db.execute(
                    "SELECT 1 FROM actions WHERE run_id=? AND effect_hash=? AND approval_id IS NOT NULL AND status!='failed'",
                    (run_id, fingerprint(effect)),
                ).fetchone()
            ):
                raise DuplicateAction(
                    "Consequential effect already dispatched; inspect its outcome"
                )
            if requires_approval:
                row = db.execute(
                    "SELECT * FROM approvals WHERE id=?", (approval_id,)
                ).fetchone()
                if (
                    not row
                    or row["status"] != "approved"
                    or time.time() >= row["expires"]
                ):
                    raise ApprovalError("A current explicit approval is required")
                expected = (
                    run_id,
                    action_id,
                    fingerprint(action),
                    fingerprint(effect),
                    str(generation),
                )
                observed = tuple(
                    row[k]
                    for k in (
                        "run_id",
                        "action_id",
                        "action_hash",
                        "effect_hash",
                        "generation",
                    )
                )
                if expected != observed:
                    raise ApprovalError(
                        "Action, effect or browser generation changed; reapprove"
                    )
                db.execute(
                    "UPDATE approvals SET status='consumed' WHERE id=?", (approval_id,)
                )
            db.execute(
                "INSERT INTO actions VALUES (?,?,?,?,?,?,?,'dispatched',?,NULL,?)",
                (
                    action_id,
                    run_id,
                    fingerprint(action),
                    fingerprint(effect),
                    fingerprint(effect_identity(effect)),
                    str(generation),
                    approval_id,
                    canonical({"action": action, "effect": effect}),
                    time.time(),
                ),
            )

    def finish_action(self, action_id: str, status: str, evidence: Any = None) -> None:
        if status not in {"verified", "observed", "uncertain", "failed", "interrupted"}:
            raise ValueError("Unsupported terminal action state")
        with self._transaction() as db:
            row = db.execute(
                "SELECT status FROM actions WHERE id=?", (action_id,)
            ).fetchone()
            if not row:
                raise AdmissionError("Cannot finish an undispatched action")
            if row["status"] == "verified" and status != "verified":
                raise AdmissionError("Verified outcome cannot rewind")
            db.execute(
                "UPDATE actions SET status=?,evidence=? WHERE id=?",
                (status, canonical(evidence), action_id),
            )

    def action(self, action_id: str) -> dict | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM actions WHERE id=?", (action_id,)
            ).fetchone()
            return dict(row) if row else None

    def actions_for_run(self, run_id: str) -> list[dict]:
        """Bounded complete-run lookup; the extra row exposes overflow to callers."""
        with self._connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM actions WHERE run_id=? ORDER BY created,id LIMIT 241",
                    (run_id,),
                )
            ]

    def unresolved_actions(self, run_id: str) -> list[dict]:
        with self._connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM actions WHERE run_id=? AND status IN ('dispatched','uncertain','interrupted')",
                    (run_id,),
                )
            ]
