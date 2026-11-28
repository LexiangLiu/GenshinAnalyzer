from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .models import AccountSnapshot


_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_snapshots (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class AccountStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(account_snapshots)").fetchall()}
            if "schema_version" not in columns:
                db.execute("ALTER TABLE account_snapshots ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")
            db.execute("CREATE INDEX IF NOT EXISTS idx_account_snapshots_updated ON account_snapshots(updated_at)")

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

    def save(self, snapshot: AccountSnapshot, name: str = "默认账号") -> str:
        snapshot_id = str(uuid4())
        with self._connect() as db:
            db.execute(
                "INSERT INTO account_snapshots (id, name, data) VALUES (?, ?, ?)",
                (snapshot_id, name, json.dumps(snapshot.to_dict(), ensure_ascii=False)),
            )
        return snapshot_id

    def update(self, snapshot_id: str, snapshot: AccountSnapshot, name: str | None = None) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id FROM account_snapshots WHERE id = ?", (snapshot_id,),
            ).fetchone()
            if row is None:
                raise ValueError("账号快照不存在")
            if name is None:
                db.execute(
                    "UPDATE account_snapshots SET data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(snapshot.to_dict(), ensure_ascii=False), snapshot_id),
                )
            else:
                db.execute(
                    "UPDATE account_snapshots SET name = ?, data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (name, json.dumps(snapshot.to_dict(), ensure_ascii=False), snapshot_id),
                )

    def get(self, snapshot_id: str) -> dict:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT id, name, data, created_at, updated_at FROM account_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise ValueError("账号快照不存在")
        return {
            "id": row["id"],
            "name": row["name"],
            "snapshot": AccountSnapshot.from_dict(json.loads(row["data"])).to_dict(),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def export(self, snapshot_id: str) -> dict:
        stored = self.get(snapshot_id)
        return {
            "schema_version": 1,
            "export_format": "genshin-analyzer-account",
            "snapshot": stored["snapshot"],
            "metadata": {
                "name": stored["name"],
                "created_at": stored["created_at"],
                "updated_at": stored["updated_at"],
            },
        }

    def list(self) -> list[dict]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT id, name, created_at, updated_at FROM account_snapshots ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]
