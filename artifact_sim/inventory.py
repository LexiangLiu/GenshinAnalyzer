from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from math import isfinite
from pathlib import Path
from uuid import uuid4

from .equipment import loadout
from .game_data import character_profile, set_id
from .models import Artifact
from .scoring import score_weights
from .simulation import Simulator


_DEFAULTS = {"language": "zh", "score_rules": ["default"], "high_score": 35}
_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS artifacts (
    ordinal INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    slot TEXT NOT NULL,
    state TEXT NOT NULL,
    locked INTEGER NOT NULL,
    is_new INTEGER NOT NULL,
    equipped TEXT REFERENCES profiles(name) ON DELETE SET NULL ON UPDATE CASCADE,
    deleted_order INTEGER UNIQUE,
    UNIQUE (equipped, slot)
);
CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);
"""


def _preferences(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - _DEFAULTS.keys():
        raise ValueError("未知的设置项")
    prefs = {**_DEFAULTS, **data}
    if prefs["language"] not in ("zh", "en"):
        raise ValueError("语言只能是 zh 或 en")
    rules = prefs["score_rules"]
    if not isinstance(rules, list) or not all(isinstance(rule, str) for rule in rules):
        raise ValueError("评分规则必须是名称列表")
    score_weights(rules)
    threshold = prefs["high_score"]
    if type(threshold) not in (int, float) or not isfinite(threshold) or threshold < 0:
        raise ValueError("高分线必须是有限的非负数")
    return prefs


class Inventory:
    """Local, transactional artifact collection. Each call opens its own SQLite connection."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        with self._connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self, *, write: bool = False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield db
        finally:
            db.close()

    @staticmethod
    def _prefs(db) -> dict:
        row = db.execute("SELECT data FROM preferences WHERE id = 1").fetchone()
        return json.loads(row["data"]) if row is not None else _preferences({})

    @staticmethod
    def _find(db, artifact_id: str):
        row = db.execute("SELECT * FROM artifacts WHERE id = ? AND deleted_order IS NULL", (artifact_id,)).fetchone()
        if row is None:
            raise ValueError(f"库存中没有圣遗物：{artifact_id}")
        return row

    @staticmethod
    def _artifact(row) -> Artifact:
        return Artifact.from_state(json.loads(row["state"]))

    @staticmethod
    def _present(row, prefs: dict) -> dict:
        data = Inventory._artifact(row).describe(prefs["language"], prefs["score_rules"])
        return {
            "id": row["id"], "locked": bool(row["locked"]), "is_new": bool(row["is_new"]),
            "equipped": row["equipped"], "high_score": data["score"] >= prefs["high_score"], **data,
        }

    @staticmethod
    def _insert(db, artifact: Artifact, artifact_id: str, *, locked=False, is_new=True, equipped=None, deleted_order=None):
        db.execute(
            "INSERT INTO artifacts (id, slot, state, locked, is_new, equipped, deleted_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, artifact.slot, json.dumps(artifact.to_state()), locked, is_new, equipped, deleted_order),
        )

    def add(self, artifacts) -> list[str]:
        ids = []
        with self._connect(write=True) as db:
            for artifact in artifacts:
                artifact_id = str(uuid4())
                self._insert(db, artifact, artifact_id)
                ids.append(artifact_id)
        return ids

    def get(self, artifact_id: str) -> dict:
        with self._connect() as db:
            return self._present(self._find(db, artifact_id), self._prefs(db))

    def artifacts(self) -> list[tuple[str, Artifact]]:
        """Return active artifacts as ``(id, Artifact)`` pairs for planning."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, state FROM artifacts WHERE deleted_order IS NULL ORDER BY ordinal"
            ).fetchall()
            return [(row["id"], self._artifact(row)) for row in rows]

    def equipped_artifacts(self, name: str) -> list[tuple[str, Artifact]]:
        """Return one profile's equipped artifacts as ``(id, Artifact)`` pairs."""
        name = character_profile(name)
        with self._connect() as db:
            return [(row["id"], self._artifact(row)) for row in self._equipped(db, name).values()]

    def list(self, *, slots=(), main_stats=(), sets=(), sort="lvdesc") -> list[dict]:
        set_ids = {set_id(name) for name in sets}
        with self._connect() as db:
            prefs = self._prefs(db)
            rows = db.execute("SELECT * FROM artifacts WHERE deleted_order IS NULL ORDER BY ordinal")
            items = [self._present(row, prefs) for row in rows]
        filtered = [
            item for item in items
            if (not slots or item["state"]["slot"] in slots)
            and (not main_stats or item["state"]["main_stat"] in main_stats)
            and (not set_ids or item["state"]["set_id"] in set_ids)
        ]
        keys = {
            "lvasc": lambda item: item["state"]["level"],
            "lvdesc": lambda item: -item["state"]["level"],
            "part": lambda item: _SLOTS.index(item["state"]["slot"]),
            "main": lambda item: item["state"]["main_stat"],
        }
        if sort not in keys:
            raise ValueError(f"未知排序方式：{sort}")
        return sorted(filtered, key=keys[sort])

    def counts(self) -> dict:
        items = self.list()
        fields = ("slot", "main_stat", "set_id")
        return {"total": len(items), **{field: dict(Counter(item["state"][field] for item in items)) for field in fields}}

    def flags(self, artifact_id: str, *, locked: bool | None = None, seen: bool = False) -> dict:
        with self._connect(write=True) as db:
            row = self._find(db, artifact_id)
            db.execute(
                "UPDATE artifacts SET locked = ?, is_new = ? WHERE id = ?",
                (row["locked"] if locked is None else locked, row["is_new"] and not seen, artifact_id),
            )
            return self._present(self._find(db, artifact_id), self._prefs(db))

    def enhance(self, artifact_id: str, simulator: Simulator, level: int | None = None, overrides=None) -> dict:
        with self._connect(write=True) as db:
            artifact = self._artifact(self._find(db, artifact_id))
            target = min(20, (artifact.level // 4 + 1) * 4) if level is None else level
            result = simulator.enhance(artifact, target, overrides=overrides)
            if result != artifact:
                db.execute("UPDATE artifacts SET state = ?, is_new = 0 WHERE id = ?", (json.dumps(result.to_state()), artifact_id))
            return self._present(self._find(db, artifact_id), self._prefs(db))

    def reset(self, artifact_id: str | None = None) -> list[str]:
        with self._connect(write=True) as db:
            if artifact_id is None:
                rows = db.execute("SELECT * FROM artifacts WHERE deleted_order IS NULL AND NOT locked").fetchall()
            else:
                row = self._find(db, artifact_id)
                if row["locked"]:
                    raise ValueError("已上锁的圣遗物不能重置")
                rows = [row]
            updated = []
            for row in rows:
                artifact = self._artifact(row)
                if artifact.level:
                    db.execute("UPDATE artifacts SET state = ? WHERE id = ?", (json.dumps(artifact.reset().to_state()), row["id"]))
                    updated.append(row["id"])
            return updated

    def delete(self, artifact_id: str) -> None:
        with self._connect(write=True) as db:
            row = self._find(db, artifact_id)
            if row["locked"] or row["equipped"]:
                raise ValueError("已上锁或已装备的圣遗物不能删除")
            db.execute(
                "UPDATE artifacts SET deleted_order = (SELECT coalesce(max(deleted_order), 0) + 1 FROM artifacts) WHERE id = ?",
                (artifact_id,),
            )

    def undo_delete(self) -> str | None:
        with self._connect(write=True) as db:
            row = db.execute("SELECT id FROM artifacts WHERE deleted_order IS NOT NULL ORDER BY deleted_order DESC LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE artifacts SET deleted_order = NULL WHERE id = ?", (row["id"],))
            return row["id"]

    def clear(self) -> int:
        """Permanently delete all currently unequipped, unlocked artifacts."""
        with self._connect(write=True) as db:
            return db.execute("DELETE FROM artifacts WHERE deleted_order IS NULL AND NOT locked AND equipped IS NULL").rowcount

    def preferences(self, updates: dict | None = None, *, reset=False) -> dict:
        with self._connect(write=reset or updates is not None) as db:
            prefs = self._prefs(db)
            if reset or updates is not None:
                prefs = _preferences({} if reset else {**prefs, **updates})
                db.execute("INSERT OR REPLACE INTO preferences VALUES (1, ?)", (json.dumps(prefs),))
            return prefs

    def profiles(self) -> list[str]:
        with self._connect() as db:
            return [row["name"] for row in db.execute("SELECT name FROM profiles ORDER BY name")]

    def profile(self, name: str, *, rename: str | None = None, delete=False) -> str:
        name = character_profile(name)
        with self._connect(write=True) as db:
            if delete:
                change = db.execute("DELETE FROM profiles WHERE name = ?", (name,))
            elif rename is not None:
                new_name = character_profile(rename)
                change = db.execute("UPDATE profiles SET name = ? WHERE name = ?", (new_name, name))
                name = new_name
            else:
                db.execute("INSERT OR IGNORE INTO profiles VALUES (?)", (name,))
                return name
            if not change.rowcount:
                raise ValueError("配装不存在")
        return name

    @staticmethod
    def _equipped(db, name: str):
        rows = db.execute("SELECT * FROM artifacts WHERE equipped = ? ORDER BY ordinal", (name,))
        return {row["slot"]: row for row in rows}

    @staticmethod
    def _panel(rows, prefs: dict) -> dict:
        return loadout([Inventory._artifact(row) for row in rows.values()], prefs["language"])

    def panel(self, name: str) -> dict:
        name = character_profile(name)
        with self._connect() as db:
            rows = self._equipped(db, name)
            prefs = self._prefs(db)
            return {
                "profile": name,
                "equipped": {slot: self._present(row, prefs) for slot, row in rows.items()},
                **self._panel(rows, prefs),
            }

    def equip(self, name: str, artifact_ids, *, preview: bool = False) -> dict:
        name = character_profile(name)
        with self._connect(write=not preview) as db:
            items = [self._find(db, artifact_id) for artifact_id in artifact_ids]
            if len(items) > 5 or len({row["slot"] for row in items}) != len(items):
                raise ValueError("一次配装须使用最多五个不同部位")
            prefs = self._prefs(db)
            before = self._equipped(db, name)
            after = {**before, **{row["slot"]: row for row in items}}
            swaps = [
                (row, before.get(row["slot"])) for row in items if row["equipped"] != name
            ]
            if not preview:
                db.execute("INSERT OR IGNORE INTO profiles VALUES (?)", (name,))
                for new, old in swaps:
                    db.execute("UPDATE artifacts SET equipped = NULL WHERE id = ?", (new["id"],))
                    if old is not None:
                        db.execute("UPDATE artifacts SET equipped = ? WHERE id = ?", (new["equipped"], old["id"]))
                    db.execute("UPDATE artifacts SET equipped = ? WHERE id = ?", (name, new["id"]))
            prior, proposed = self._panel(before, prefs), self._panel(after, prefs)
            old, new = prior["panel_stats"], proposed["panel_stats"]
            deltas = {
                stat: round(new.get(stat, 0) - old.get(stat, 0), 6)
                for stat in sorted(old.keys() | new.keys()) if old.get(stat, 0) != new.get(stat, 0)
            }
            return {
                "profile": name, "equipped": {slot: row["id"] for slot, row in after.items()},
                "before": prior, "after": proposed, "stat_delta": deltas,
            }

    def unequip(self, name: str, slots) -> list[str]:
        name = character_profile(name)
        if any(slot not in _SLOTS for slot in slots):
            raise ValueError("存在未知的装备部位")
        with self._connect(write=True) as db:
            rows = self._equipped(db, name)
            ids = [row["id"] for slot, row in rows.items() if not slots or slot in slots]
            db.executemany("UPDATE artifacts SET equipped = NULL WHERE id = ?", ((artifact_id,) for artifact_id in ids))
            return ids

    def export(self) -> dict:
        with self._connect() as db:
            artifacts = [
                {
                    "id": row["id"], "state": json.loads(row["state"]), "locked": bool(row["locked"]),
                    "is_new": bool(row["is_new"]), "equipped": row["equipped"], "deleted_order": row["deleted_order"],
                }
                for row in db.execute("SELECT * FROM artifacts ORDER BY ordinal")
            ]
            return {
                "inventory_schema": 1, "artifacts": artifacts, "preferences": self._prefs(db),
                "profiles": [row["name"] for row in db.execute("SELECT name FROM profiles ORDER BY name")],
            }

    def restore(self, backup: dict, *, replace=False) -> int:
        if not isinstance(backup, dict) or backup.get("inventory_schema") != 1:
            raise ValueError("不支持的库存备份格式")
        profiles = backup["profiles"]
        if not isinstance(profiles, list) or not all(isinstance(name, str) and name.strip() for name in profiles):
            raise ValueError("无效的配装列表")
        prefs = _preferences(backup["preferences"])
        artifacts = backup["artifacts"]
        if not isinstance(artifacts, list):
            raise ValueError("无效的圣遗物列表")
        with self._connect(write=True) as db:
            if replace:
                self._wipe(db)
            db.executemany("INSERT OR IGNORE INTO profiles VALUES (?)", ((name,) for name in profiles))
            offset = db.execute("SELECT coalesce(max(deleted_order), 0) FROM artifacts").fetchone()[0]
            for item in artifacts:
                artifact_id, locked, is_new = item["id"], item["locked"], item["is_new"]
                deleted_order, equipped = item["deleted_order"], item["equipped"]
                if not isinstance(artifact_id, str) or not artifact_id or type(locked) is not bool or type(is_new) is not bool:
                    raise ValueError("圣遗物标识或状态无效")
                if equipped is not None and equipped not in profiles:
                    raise ValueError("备份中缺少圣遗物的配装")
                if deleted_order is not None:
                    if equipped is not None or locked or type(deleted_order) is not int or deleted_order < 1:
                        raise ValueError("备份中的删除记录无效")
                    deleted_order += offset
                self._insert(db, Artifact.from_state(item["state"]), artifact_id, locked=locked, is_new=is_new,
                             equipped=equipped, deleted_order=deleted_order)
            if replace:
                db.execute("INSERT INTO preferences VALUES (1, ?)", (json.dumps(prefs),))
        return len(artifacts)

    @staticmethod
    def _wipe(db) -> None:
        for table in ("artifacts", "profiles", "preferences"):
            db.execute(f"DELETE FROM {table}")

    def wipe(self) -> None:
        with self._connect(write=True) as db:
            self._wipe(db)
