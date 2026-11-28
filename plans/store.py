from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping
from uuid import uuid4


_SCHEMA = """
CREATE TABLE IF NOT EXISTS growth_plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    account_snapshot_id TEXT,
    goal_id TEXT,
    data TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _hash(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class PlanStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(growth_plans)").fetchall()}
            if "schema_version" not in columns:
                db.execute("ALTER TABLE growth_plans ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")
            db.execute("CREATE INDEX IF NOT EXISTS idx_growth_plans_created ON growth_plans(created_at)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA journal_mode = WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(
        self,
        data: Mapping[str, object],
        *,
        name: str = "养成方案",
        account_snapshot_id: str | None = None,
        goal_id: str | None = None,
    ) -> str:
        plan_id = str(uuid4())
        encoded = json.dumps(dict(data), ensure_ascii=False)
        with self._connect() as db:
            db.execute(
                "INSERT INTO growth_plans (id, name, account_snapshot_id, goal_id, data, input_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    plan_id,
                    name,
                    account_snapshot_id,
                    goal_id,
                    encoded,
                    _hash(dict(data)),
                ),
            )
        return plan_id

    def get(self, plan_id: str) -> dict:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT id, name, account_snapshot_id, goal_id, data, input_hash, created_at "
                "FROM growth_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            raise ValueError("养成方案不存在")
        data = json.loads(row["data"])
        if isinstance(data, dict) and int(data.get("schema_version", 1)) != 1:
            raise ValueError("不支持的养成方案版本")
        return {
            "id": row["id"],
            "name": row["name"],
            "account_snapshot_id": row["account_snapshot_id"],
            "goal_id": row["goal_id"],
            "data": data,
            "input_hash": row["input_hash"],
            "created_at": row["created_at"],
        }

    def list(self) -> list[dict]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT id, name, account_snapshot_id, goal_id, input_hash, created_at "
                "FROM growth_plans ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _summary(plan: dict) -> dict:
        data = plan["data"]
        comparison = data.get("comparison", {})
        return {
            "plan_id": plan["id"],
            "name": plan["name"],
            "schema_version": data.get("schema_version", 1),
            "account_snapshot_id": plan["account_snapshot_id"],
            "goal_id": plan["goal_id"],
            "created_at": plan["created_at"],
            "cost": comparison.get("cost", {}),
            "expected_benefit": comparison.get("expected_benefit", "unknown"),
            "risk": comparison.get("risk", {"interval": "unknown"}),
            "missing": comparison.get("missing", []),
            "feasibility": comparison.get("feasibility", "unknown"),
        }

    def compare(self, first_id: str, second_id: str) -> dict:
        first = self.get(first_id)
        second = self.get(second_id)
        return {
            "changed": first["input_hash"] != second["input_hash"],
            "first_hash": first["input_hash"],
            "second_hash": second["input_hash"],
            "first": self._summary(first),
            "second": self._summary(second),
        }
