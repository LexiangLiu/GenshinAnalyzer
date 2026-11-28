from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .models import AccountGoal


_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_goals (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class GoalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(account_goals)").fetchall()}
            if "schema_version" not in columns:
                db.execute("ALTER TABLE account_goals ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")
            db.execute("CREATE INDEX IF NOT EXISTS idx_account_goals_updated ON account_goals(updated_at)")

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

    def save(self, goal: AccountGoal) -> str:
        goal_id = str(uuid4())
        with self._connect() as db:
            db.execute(
                "INSERT INTO account_goals (id, name, data) VALUES (?, ?, ?)",
                (goal_id, goal.name, json.dumps(goal.to_dict(), ensure_ascii=False)),
            )
        return goal_id

    def get(self, goal_id: str) -> dict:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT id, name, data, created_at, updated_at "
                "FROM account_goals WHERE id = ?",
                (goal_id,),
            ).fetchone()
        if row is None:
            raise ValueError("养成目标不存在")
        return {
            "id": row["id"],
            "name": row["name"],
            "goal": json.loads(row["data"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update(self, goal_id: str, goal: AccountGoal) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id FROM account_goals WHERE id = ?", (goal_id,),
            ).fetchone()
            if row is None:
                raise ValueError("养成目标不存在")
            db.execute(
                "UPDATE account_goals SET name = ?, data = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (goal.name, json.dumps(goal.to_dict(), ensure_ascii=False), goal_id),
            )

    def list(self) -> list[dict]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT id, name, created_at, updated_at "
                "FROM account_goals ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]
