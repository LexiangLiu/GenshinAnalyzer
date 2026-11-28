from __future__ import annotations

from pathlib import Path
from typing import Mapping
import hashlib
import json
import sqlite3
from contextlib import contextmanager

from account.models import AccountSnapshot, CharacterState
from account.store import AccountStore
from goals.models import AccountGoal
from goals.store import GoalStore
from planning_v2.service import calculate_growth_plan
from plans.store import PlanStore
from .backup import PersistenceBackup


class PlanningService:
    """Persisted application boundary without subprocesses or external account imports."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.accounts = AccountStore(self.root / "accounts.sqlite3")
        self.goals = GoalStore(self.root / "accounts.sqlite3")
        self.plans = PlanStore(self.root / "accounts.sqlite3")
        self.persistence = PersistenceBackup(self.root)
        self._cache_db = self.root / "calculation_cache.sqlite3"
        with self._cache_connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS calculation_cache (
                    cache_key TEXT PRIMARY KEY,
                    account_snapshot_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_calc_cache_snapshot_goal ON calculation_cache(account_snapshot_id, goal_id)")

    @contextmanager
    def _cache_connect(self):
        db = sqlite3.connect(self._cache_db)
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA journal_mode = WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _cache_key(
        snapshot: Mapping[str, object],
        goal: Mapping[str, object],
        inventory: Mapping[str, object] | None,
        engine_results: Mapping[str, object] | None,
    ) -> str:
        payload = {
            "snapshot": snapshot,
            "goal": goal,
            "inventory": inventory or {},
            "engine_results": engine_results or {},
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def invalidate_cache(
        self,
        *,
        account_snapshot_id: str | None = None,
        goal_id: str | None = None,
    ) -> int:
        with self._cache_connect() as db:
            if account_snapshot_id is not None and goal_id is not None:
                cursor = db.execute(
                    "DELETE FROM calculation_cache WHERE account_snapshot_id = ? AND goal_id = ?",
                    (account_snapshot_id, goal_id),
                )
            elif account_snapshot_id is not None:
                cursor = db.execute(
                    "DELETE FROM calculation_cache WHERE account_snapshot_id = ?",
                    (account_snapshot_id,),
                )
            elif goal_id is not None:
                cursor = db.execute(
                    "DELETE FROM calculation_cache WHERE goal_id = ?",
                    (goal_id,),
                )
            else:
                cursor = db.execute("DELETE FROM calculation_cache")
            return cursor.rowcount

    def calculate_and_save(
        self,
        *,
        account_snapshot_id: str,
        goal_id: str,
        name: str = "养成方案",
        inventory: Mapping[str, object] | None = None,
        engine_results: Mapping[str, object] | None = None,
    ) -> dict:
        account = self.accounts.get(account_snapshot_id)
        goal_row = self.goals.get(goal_id)
        goal = AccountGoal.from_dict(goal_row["goal"])
        cache_key = self._cache_key(
            account["snapshot"],
            goal_row["goal"],
            inventory,
            engine_results,
        )
        with self._cache_connect() as db:
            row = db.execute(
                "SELECT plan_id FROM calculation_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is not None:
            try:
                plan = self.plans.get(row[0])
                return {
                    "plan_id": row[0],
                    "plan": plan,
                    "cache": "hit",
                    "invalidation_scope": "none",
                }
            except ValueError:
                with self._cache_connect() as db:
                    db.execute(
                        "DELETE FROM calculation_cache WHERE cache_key = ?",
                        (cache_key,),
                    )
        result = calculate_growth_plan(
            goal,
            account["snapshot"],
            inventory=inventory,
            engine_results=engine_results,
        )
        plan_id = self.plans.save(
            result,
            name=name,
            account_snapshot_id=account_snapshot_id,
            goal_id=goal_id,
        )
        with self._cache_connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO calculation_cache "
                "(cache_key, account_snapshot_id, goal_id, plan_id) VALUES (?, ?, ?, ?)",
                (cache_key, account_snapshot_id, goal_id, plan_id),
            )
        return {
            "plan_id": plan_id,
            "plan": self.plans.get(plan_id),
            "cache": "miss",
            "invalidation_scope": "account_snapshot_or_goal_changed",
        }

    def update_snapshot(
        self,
        account_snapshot_id: str,
        *,
        character_updates: Mapping[str, Mapping[str, object]] | None = None,
        inventory_updates: Mapping[str, object] | None = None,
        equipment_updates: Mapping[str, object] | None = None,
        metadata_updates: Mapping[str, object] | None = None,
        name: str | None = None,
    ) -> dict:
        """Create a new snapshot for a partial state update; retain the old snapshot."""
        if not isinstance(character_updates, Mapping):
            character_updates = {}
        account = self.accounts.get(account_snapshot_id)
        old = AccountSnapshot.from_dict(account["snapshot"])
        characters = []
        changed_characters = set()
        for character in old.characters:
            update = character_updates.get(character.identity)
            if update is None:
                characters.append(character)
                continue
            changed_characters.add(character.identity)
            talents = dict(character.talents)
            if isinstance(update.get("talents"), Mapping):
                talents.update(update["talents"])
            characters.append(CharacterState(
                identity=character.identity,
                level=int(update.get("level", character.level)),
                ascension=int(update.get("ascension", character.ascension)),
                constellation=int(update.get("constellation", character.constellation)),
                talents=talents,
                weapon_id=update.get("weapon_id", character.weapon_id),
                weapon_level=(
                    int(update["weapon_level"])
                    if update.get("weapon_level") is not None
                    else character.weapon_level
                ),
                weapon_refinement=(
                    int(update["weapon_refinement"])
                    if update.get("weapon_refinement") is not None
                    else character.weapon_refinement
                ),
                equipped_artifact_ids=tuple(
                    update.get(
                        "equipped_artifact_ids",
                        character.equipped_artifact_ids,
                    )
                ),
            ))
        if character_updates and not changed_characters:
            raise ValueError("账号快照中没有可更新的目标角色")

        inventory = dict(old.inventory)
        if isinstance(inventory_updates, Mapping):
            inventory.update(inventory_updates)
        equipment = dict(old.equipment)
        if isinstance(equipment_updates, Mapping):
            equipment.update(equipment_updates)
        metadata = dict(old.metadata)
        if isinstance(metadata_updates, Mapping):
            metadata.update(metadata_updates)
        updated = AccountSnapshot(
            source=dict(old.source),
            characters=characters,
            inventory=inventory,
            equipment=equipment,
            metadata=metadata,
            schema_version=old.schema_version,
        )
        new_id = self.accounts.save(
            updated,
            name or f"{account['name']}（更新）",
        )
        invalidated = self.invalidate_cache(account_snapshot_id=account_snapshot_id)
        return {
            "previous_snapshot_id": account_snapshot_id,
            "snapshot_id": new_id,
            "snapshot": self.accounts.get(new_id),
            "changed_characters": sorted(changed_characters),
            "invalidated_cache_entries": invalidated,
            "invalidation_scope": (
                f"account_snapshot:{account_snapshot_id}"
                if changed_characters or inventory_updates or equipment_updates or metadata_updates
                else "none"
            ),
        }

    def update_goal(self, goal_id: str, goal: AccountGoal) -> dict:
        """Update a goal in place and invalidate every cache associated with it."""
        if not isinstance(goal, AccountGoal):
            raise ValueError("goal 必须是 AccountGoal")
        self.goals.update(goal_id, goal)
        invalidated = self.invalidate_cache(goal_id=goal_id)
        return {
            "goal_id": goal_id,
            "goal": self.goals.get(goal_id),
            "invalidated_cache_entries": invalidated,
            "invalidation_scope": f"goal:{goal_id}",
        }

    def recalculate(
        self,
        *,
        account_snapshot_id: str,
        goal_id: str,
        name: str = "更新后养成方案",
        inventory: Mapping[str, object] | None = None,
        engine_results: Mapping[str, object] | None = None,
    ) -> dict:
        return self.calculate_and_save(
            account_snapshot_id=account_snapshot_id,
            goal_id=goal_id,
            name=name,
            inventory=inventory,
            engine_results=engine_results,
        )

    def list_plans(self) -> list[dict]:
        return self.plans.list()

    def health(self) -> dict:
        return self.persistence.health()

    def backup(self, destination: str | Path) -> dict:
        return self.persistence.create(destination)

    def restore(self, backup_dir: str | Path) -> dict:
        return self.persistence.restore(backup_dir, replace=True)

    def compare_plans(self, first_id: str, second_id: str) -> dict:
        return self.plans.compare(first_id, second_id)

    def decision_query(
        self,
        query: str,
        *,
        plan_id: str | None = None,
        second_plan_id: str | None = None,
    ) -> dict:
        if query not in (
            "weapon_or_character",
            "next_domain",
            "next_goal",
            "missing_resources",
            "plan_difference",
        ):
            raise ValueError("未知养成决策查询")
        if plan_id is None:
            plans = self.plans.list()
            if not plans:
                raise ValueError("尚无可查询方案")
            plan_id = plans[0]["id"]
        plan = self.plans.get(plan_id)
        data = plan["data"]

        if query == "weapon_or_character":
            candidates = [
                action for action in data.get("actions", [])
                if action["action_type"] in ("weapon_upgrade", "character_ascension", "talent_upgrade")
            ]
            ranked = sorted(
                candidates,
                key=lambda action: (
                    action["feasibility"] not in ("ready", "cost_remaining"),
                    -float(action["priority"]),
                ),
            )
            return {
                "query": query,
                "recommendation": ranked[0] if ranked else None,
                "candidates": ranked,
                "confidence": data.get("confidence", {}),
                "coverage": data.get("coverage", {}),
                "method": data.get("method", {}),
            }
        if query == "next_domain":
            artifact_actions = [
                action for action in data.get("actions", [])
                if action["action_type"] == "artifact_farming"
            ]
            return {
                "query": query,
                "recommendation": None,
                "reason": "秘境目标未重算；现有配装/账号刷本引擎未在本次资源方案中调用。",
                "candidates": artifact_actions,
                "confidence": data.get("confidence", {}),
            }
        if query == "next_goal":
            short_route = data.get("routes", {}).get("short", [])
            candidates = [
                action for action in short_route
                if action["feasibility"] in ("ready", "cost_remaining")
            ]
            return {
                "query": query,
                "recommendation": candidates[0] if candidates else None,
                "candidates": short_route,
                "missing": data.get("warnings", []),
                "confidence": data.get("confidence", {}),
            }
        if query == "missing_resources":
            return {
                "query": query,
                "lines": data.get("resources", {}).get("lines", []),
                "missing": data.get("resources", {}).get("unknown", []),
                "activities": data.get("activities", {}),
                "confidence": data.get("confidence", {}),
            }
        if not second_plan_id:
            raise ValueError("plan_difference 需要 second_plan_id")
        comparison = self.compare_plans(plan_id, second_plan_id)
        return {
            "query": query,
            "first_plan_id": plan_id,
            "second_plan_id": second_plan_id,
            "comparison": comparison,
            "confidence": {
                "first": plan["data"].get("confidence", {}),
                "second": self.plans.get(second_plan_id)["data"].get("confidence", {}),
            },
        }
