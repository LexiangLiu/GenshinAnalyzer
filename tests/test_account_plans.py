import tempfile
import unittest
from pathlib import Path
import sqlite3

from account import AccountSnapshot, AccountStore, CharacterState
from goals import AccountGoal, BuildGoal, CharacterGoal, GoalStore
from plans import PlanStore


class AccountPlanPrototypeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.character = CharacterState(
            identity="Neuvillette",
            level=90,
            ascension=6,
            constellation=0,
            talents={"auto": 10, "skill": 10, "burst": 10},
            weapon_id="Tome of the Eternal Flow",
        )
        self.snapshot = AccountSnapshot(
            source={"kind": "simulation", "reproducible_seed": 73},
            characters=[self.character],
            inventory={"artifact_count": 180},
            metadata={"game_version": "offline-test", "missing_fields": ["mora"]},
        )

    def test_snapshot_round_trip_and_update(self):
        store = AccountStore(self.root / "prototype.sqlite3")
        snapshot_id = store.save(self.snapshot, "测试账号")
        loaded = store.get(snapshot_id)
        self.assertEqual(loaded["snapshot"]["characters"][0]["identity"], "Neuvillette")
        updated = AccountSnapshot.from_dict(
            {
                **loaded["snapshot"],
                "metadata": {"missing_fields": []},
            }
        )
        store.update(snapshot_id, updated, "更新账号")
        self.assertEqual(store.get(snapshot_id)["name"], "更新账号")
        self.assertEqual(store.get(snapshot_id)["snapshot"]["metadata"]["missing_fields"], [])

    def test_goal_round_trip(self):
        goal = AccountGoal(
            name="那维莱特养成",
            horizon="mixed",
            characters=[
                CharacterGoal(
                    character_id="Neuvillette",
                    target_level=90,
                    target_constellation=0,
                    target_talents={"auto": 10, "skill": 10, "burst": 10},
                    build=BuildGoal(
                        weapon_id="Tome of the Eternal Flow",
                        weapon_target_level=90,
                        four_piece_set=15031,
                        substat_weights={"CRIT_RATE": 1.0, "CRIT_DMG": 1.0},
                    ),
                )
            ],
            status="active",
        )
        store = GoalStore(self.root / "prototype.sqlite3")
        goal_id = store.save(goal)
        loaded = store.get(goal_id)
        self.assertEqual(loaded["goal"]["characters"][0]["character_id"], "Neuvillette")
        self.assertEqual(loaded["goal"]["characters"][0]["build"]["four_piece_set"], 15031)

    def test_plan_save_compare_and_hash(self):
        store = PlanStore(self.root / "prototype.sqlite3")
        first_id = store.save(
            {"recommended_domain_id": 982, "actions": [{"type": "domain", "cost_resin": 200}]},
            name="方案 A",
        )
        second_id = store.save(
            {"recommended_domain_id": 982, "actions": [{"type": "domain", "cost_resin": 400}]},
            name="方案 B",
        )
        first = store.get(first_id)
        second = store.get(second_id)
        self.assertNotEqual(first["input_hash"], second["input_hash"])
        comparison = store.compare(first_id, second_id)
        self.assertTrue(comparison["changed"])
        self.assertEqual(comparison["first"]["name"], "方案 A")
        self.assertEqual(comparison["second"]["name"], "方案 B")

    def test_old_store_tables_are_migrated_in_place(self):
        db_path = self.root / "old.sqlite3"
        db = sqlite3.connect(db_path)
        db.executescript(
            "CREATE TABLE account_snapshots (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
            "CREATE TABLE account_goals (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
            "CREATE TABLE growth_plans (id TEXT PRIMARY KEY, name TEXT NOT NULL, account_snapshot_id TEXT, goal_id TEXT, data TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL);"
        )
        db.close()
        AccountStore(db_path)
        GoalStore(db_path)
        PlanStore(db_path)
        db = sqlite3.connect(db_path)
        for table in ("account_snapshots", "account_goals", "growth_plans"):
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
            self.assertIn("schema_version", columns)
        db.close()

    def test_external_api_kind_is_not_accepted(self):
        with self.assertRaisesRegex(ValueError, "source.kind"):
            AccountSnapshot(
                source={"kind": "external_api"},
                characters=[self.character],
            )


if __name__ == "__main__":
    unittest.main()
