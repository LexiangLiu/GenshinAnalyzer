import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from account import AccountSnapshot, CharacterState
from account import import_account_snapshot, import_account_snapshot_batch
from activities import ActivityRule, evaluate_activity_rules
from applications import PlanningService, PersistenceBackup
from goals import AccountGoal, BuildGoal, CharacterGoal
from planning_v2 import calculate_growth_plan
from damage.character_models import catalog_coverage_audit
from data.provenance import provenance_snapshot
from tools.benchmark_growth import benchmark


class ResourcePlanningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.character = CharacterState(
            identity="Neuvillette",
            level=80,
            ascension=5,
            constellation=0,
            talents={"auto": 9, "skill": 8, "burst": 9},
            weapon_id="Tome of the Eternal Flow",
            weapon_level=80,
            weapon_refinement=1,
        )
        self.snapshot = AccountSnapshot(
            source={"kind": "simulation", "reproducible_seed": 73},
            characters=[self.character],
            inventory={"items": [{"id": 202, "count": 10000}]},
            metadata={"game_version": "offline-test", "missing_fields": ["mora_inventory"]},
        )
        self.goal = AccountGoal(
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
                    short_term_priority=2.0,
                    long_term_priority=1.0,
                )
            ],
            status="active",
            activity_rules=[{"kind": "limit", "name": "示例骨架", "effective": False}],
        )

    def test_known_character_talent_weapon_and_mora_costs(self):
        result = calculate_growth_plan(
            self.goal,
            self.snapshot,
            inventory={"items": [{"id": 202, "count": 10000}]},
        )
        resource_ids = {
            str(line["resource_id"]): line
            for line in result["resources"]["lines"]
        }
        self.assertIn("202", resource_ids)
        mora = resource_ids["202"]
        self.assertGreater(mora["count"], 10000)
        self.assertIn("摩拉", mora["name"])
        self.assertGreater(len(result["resources"]["lines"]), 1)
        self.assertTrue(result["resources"]["actions"])
        self.assertEqual(
            result["resources"]["actions"][0]["resin"],
            "unknown",
        )

    def test_missing_character_is_unknown_not_zero(self):
        goal = AccountGoal(
            name="缺失角色",
            characters=[
                CharacterGoal(
                    character_id="不存在角色",
                    target_level=90,
                    target_talents={"auto": 10, "skill": 10, "burst": 10},
                )
            ],
        )
        result = calculate_growth_plan(goal, self.snapshot)
        unknown_fields = {
            item["field"] for item in result["resources"]["unknown"]
        }
        self.assertIn("character", unknown_fields)
        self.assertEqual(result["resources"]["lines"], [])

    def test_application_saves_plan_and_reloads_it(self):
        service = PlanningService(self.root)
        snapshot_id = service.accounts.save(self.snapshot, "测试账号")
        goal_id = service.goals.save(self.goal)
        outcome = service.calculate_and_save(
            account_snapshot_id=snapshot_id,
            goal_id=goal_id,
            name="阶段二方案",
        )
        plan_id = outcome["plan_id"]
        saved = service.plans.get(plan_id)
        self.assertEqual(saved["name"], "阶段二方案")
        self.assertEqual(saved["account_snapshot_id"], snapshot_id)
        self.assertEqual(saved["goal_id"], goal_id)
        self.assertIn("resources", saved["data"])

    def test_constellation_gap_is_unknown(self):
        goal = AccountGoal(
            name="命座目标",
            characters=[
                CharacterGoal(
                    character_id="Neuvillette",
                    target_level=None,
                    target_constellation=2,
                    target_talents=None,
                )
            ],
        )
        result = calculate_growth_plan(goal, self.snapshot)
        fields = {item["field"] for item in result["resources"]["unknown"]}
        self.assertIn("constellation", fields)
        self.assertIn("talents", fields)

    def test_partial_snapshot_update_creates_new_snapshot_and_plan(self):
        service = PlanningService(self.root)
        snapshot_id = service.accounts.save(self.snapshot, "测试账号")
        goal_id = service.goals.save(self.goal)
        first = service.calculate_and_save(
            account_snapshot_id=snapshot_id,
            goal_id=goal_id,
            name="旧方案",
        )
        updated = service.update_snapshot(
            snapshot_id,
            character_updates={
                "Neuvillette": {
                    "level": 90,
                    "talents": {"auto": 10, "skill": 10, "burst": 10},
                    "weapon_level": 90,
                }
            },
            metadata_updates={"last_partial_update": "character_goal_state"},
            name="更新账号",
        )
        new_snapshot = updated["snapshot"]
        self.assertEqual(new_snapshot["snapshot"]["characters"][0]["level"], 90)
        self.assertIsNotNone(service.accounts.get(snapshot_id))
        second = service.recalculate(
            account_snapshot_id=updated["snapshot_id"],
            goal_id=goal_id,
            name="新方案",
        )
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        comparison = service.compare_plans(first["plan_id"], second["plan_id"])
        self.assertTrue(comparison["changed"])

    def test_actions_include_short_long_routes_and_unknown_placeholders(self):
        result = calculate_growth_plan(
            self.goal,
            self.snapshot,
        )
        action_types = {action["action_type"] for action in result["actions"]}
        self.assertIn("character_ascension", action_types)
        self.assertIn("talent_upgrade", action_types)
        self.assertIn("weapon_upgrade", action_types)
        self.assertIn("artifact_farming", action_types)
        self.assertIn("weekly_boss", action_types)
        self.assertIn("activity", action_types)
        self.assertIn("comparison", result)
        self.assertEqual(result["comparison"]["expected_benefit"], "unknown")
        self.assertIn("short", result["routes"])
        self.assertIn("long", result["routes"])
        activity = next(action for action in result["actions"] if action["action_type"] == "activity")
        self.assertEqual(activity["feasibility"], "blocked_unknown")

    def test_activity_expired_and_unverified_rules_are_degraded(self):
        rules = [
            ActivityRule(
                rule_id="expired",
                name="已过期活动",
                source_type="offline_config",
                source_ref="offline/test.json#expired",
                valid_from="2025-01-01T00:00:00",
                valid_until="2025-01-31T23:59:59",
                confidence=0.9,
                verified=True,
            ),
            ActivityRule(
                rule_id="unverified",
                name="未验证活动",
                source_type="user_input",
                source_ref="user/session-1",
                valid_from="2026-01-01T00:00:00",
                valid_until="2027-01-31T23:59:59",
                confidence=0.95,
                verified=False,
            ),
        ]
        evaluation = evaluate_activity_rules(rules, now="2026-11-28T12:00:00")
        statuses = {item["rule"]["rule_id"]: item["status"] for item in evaluation["degraded"]}
        self.assertEqual(statuses["expired"], "expired")
        self.assertEqual(statuses["unverified"], "unverified")
        self.assertEqual(evaluation["accepted"], [])

    def test_plan_unifies_confidence_coverage_method_and_warnings(self):
        result = calculate_growth_plan(self.goal, self.snapshot)
        self.assertIn("confidence", result)
        self.assertIn("coverage", result)
        self.assertIn("method", result)
        self.assertIn("limitations", result)
        self.assertIn("warnings", result)
        self.assertEqual(result["method"]["loadout_search"], "not_run")
        self.assertEqual(result["method"]["monte_carlo"], "not_run")
        self.assertIn("formula", result["coverage"])

    def test_decision_queries_and_plan_difference(self):
        service = PlanningService(self.root)
        snapshot_id = service.accounts.save(self.snapshot, "测试账号")
        goal_id = service.goals.save(self.goal)
        first = service.calculate_and_save(
            account_snapshot_id=snapshot_id,
            goal_id=goal_id,
            name="方案一",
        )
        updated = service.update_snapshot(
            snapshot_id,
            character_updates={
                "Neuvillette": {
                    "level": 90,
                    "talents": {"auto": 10, "skill": 10, "burst": 10},
                }
            },
        )
        second = service.recalculate(
            account_snapshot_id=updated["snapshot_id"],
            goal_id=goal_id,
            name="方案二",
        )
        weapon_query = service.decision_query(
            "weapon_or_character",
            plan_id=first["plan_id"],
        )
        self.assertIn("recommendation", weapon_query)
        missing_query = service.decision_query(
            "missing_resources",
            plan_id=first["plan_id"],
        )
        self.assertIn("missing", missing_query)
        difference = service.decision_query(
            "plan_difference",
            plan_id=first["plan_id"],
            second_plan_id=second["plan_id"],
        )
        self.assertIn("comparison", difference)

    def test_calculation_cache_hits_and_partial_update_invalidates(self):
        service = PlanningService(self.root)
        snapshot_id = service.accounts.save(self.snapshot, "测试账号")
        goal_id = service.goals.save(self.goal)
        first = service.calculate_and_save(
            account_snapshot_id=snapshot_id,
            goal_id=goal_id,
        )
        second = service.calculate_and_save(
            account_snapshot_id=snapshot_id,
            goal_id=goal_id,
        )
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(first["cache"], "miss")
        self.assertEqual(second["cache"], "hit")
        updated = service.update_snapshot(
            snapshot_id,
            character_updates={
                "Neuvillette": {"level": 90},
            },
        )
        self.assertGreaterEqual(updated["invalidated_cache_entries"], 1)
        recomputed = service.recalculate(
            account_snapshot_id=updated["snapshot_id"],
            goal_id=goal_id,
        )
        self.assertEqual(recomputed["cache"], "miss")

    def test_existing_engine_results_are_integrated_without_fake_precision(self):
        result = calculate_growth_plan(
            self.goal,
            self.snapshot,
            engine_results={
                "loadout": {"total_objective": 1234, "search": {"exact": True, "nodes": 42}},
                "formula": {"mode": "relative", "formulas": [{"key": "test"}]},
                "expectation": {"method": {"confidence_interval": "normal"}},
                "monte_carlo": {
                    "method": {
                        "mean_interval": "approximate 95%",
                        "maximum_searches": 192,
                        "samples": 12,
                        "limitations": ["仅概率抽样误差"],
                    },
                    "rankings": [{"domain_id": 982}],
                },
            },
        )
        self.assertEqual(result["method"]["loadout_search"], "completed")
        self.assertEqual(result["method"]["formula_weighting"], "completed")
        self.assertEqual(result["method"]["expectation"], "completed")
        self.assertEqual(result["method"]["monte_carlo"], "completed")
        self.assertIn("仅概率抽样误差", result["comparison"]["risk"]["engine_limitations"])
        self.assertNotEqual(result["comparison"]["expected_benefit"], "unknown")

    def test_structured_account_import_rejects_external_api_and_accepts_manual(self):
        manual = import_account_snapshot({
            "source": {"kind": "manual", "imported_at": "2026-11-28"},
            "characters": [{
                "identity": "Neuvillette",
                "level": 80,
                "ascension": 5,
                "constellation": 0,
                "talents": {"auto": 9, "skill": 8, "burst": 9},
            }],
            "inventory": {"items": []},
            "equipment": {},
            "metadata": {"missing_fields": ["mora_inventory"]},
        })
        self.assertEqual(manual.source["kind"], "manual")
        self.assertIn("import_source", manual.metadata["missing_fields"])
        with self.assertRaisesRegex(ValueError, "external_api"):
            import_account_snapshot({
                "source": {"kind": "external_api"},
                "characters": [],
            })

    def test_batch_account_import_reports_partial_success_and_duplicates(self):
        valid = {
            "source": {"kind": "manual", "external_id": "manual-1"},
            "characters": [{
                "identity": "Neuvillette",
                "level": 90,
                "ascension": 6,
                "constellation": 0,
                "talents": {"auto": 10, "skill": 10, "burst": 10},
            }],
            "inventory": {},
            "equipment": {},
            "metadata": {},
        }
        duplicate = {
            **valid,
            "source": {"kind": "manual", "external_id": "manual-1"},
        }
        batch = import_account_snapshot_batch({
            "snapshots": [
                valid,
                duplicate,
                {"source": {"kind": "external_api"}, "characters": []},
            ],
        })
        self.assertEqual(batch["counts"]["total"], 3)
        self.assertEqual(batch["counts"]["accepted"], 1)
        self.assertEqual(batch["counts"]["rejected"], 2)
        self.assertTrue(any("重复来源标识" in row["error"] for row in batch["rejected"]))

    def test_formula_catalog_coverage_audit_is_versioned_and_read_only(self):
        audit = catalog_coverage_audit()
        self.assertEqual(audit["schema_version"], 1)
        self.assertEqual(audit["catalog_schema_version"], 1)
        self.assertGreater(audit["model_count"], 0)
        self.assertGreater(audit["character_count"], 0)
        self.assertIsInstance(audit["uncalibrated_characters"], list)
        self.assertIsInstance(audit["missing_explicit_coverage_record"], list)
        for row in audit["characters"]:
            self.assertIn(row["status"], ("calibrated", "uncalibrated"))
            self.assertIsInstance(row["models"], list)
            self.assertIsInstance(row["categories"], list)

    def test_provenance_snapshot_has_game_formula_activity_and_unknown_sources(self):
        provenance = provenance_snapshot([
            {
                "rule_id": "offline-rule",
                "name": "离线规则",
                "source_type": "offline_config",
                "source_ref": "offline/activity.json",
                "valid_from": "2026-01-01T00:00:00",
                "valid_until": "2026-12-31T23:59:59",
                "confidence": 0.9,
                "verified": True,
            },
            {"kind": "incomplete"},
        ])
        self.assertEqual(provenance["schema_version"], 1)
        self.assertEqual(provenance["game_data"]["game_version"], "7.2")
        self.assertGreater(provenance["formula_catalog"]["model_count"], 0)
        statuses = {item["rule_id"]: item["status"] for item in provenance["activities"]}
        self.assertEqual(statuses["offline-rule"], "provenance_complete")
        self.assertEqual(statuses["incomplete"], "provenance_incomplete")
        unknown_fields = {item["field"] for item in provenance["unknown"]}
        self.assertIn("real_account_sync", unknown_fields)
        self.assertIn("live_activity_fetch", unknown_fields)

    def test_growth_benchmark_returns_reproducible_report_shape(self):
        report = benchmark(repeats=1)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["repeats"], 1)
        self.assertIn("coverage_audit", report["operations"])
        self.assertIn("planning_calculate_and_save", report["operations"])
        self.assertIn("persistence_backup", report["operations"])
        for metrics in report["operations"].values():
            self.assertGreaterEqual(metrics["mean_seconds"], 0)
            self.assertGreaterEqual(metrics["min_seconds"], 0)

    def test_goal_update_invalidates_cached_plan(self):
        service = PlanningService(self.root)
        snapshot_id = service.accounts.save(self.snapshot, "测试账号")
        goal_id = service.goals.save(self.goal)
        first = service.calculate_and_save(
            account_snapshot_id=snapshot_id,
            goal_id=goal_id,
        )
        self.assertEqual(first["cache"], "miss")
        changed = AccountGoal(
            name="更新养成目标",
            horizon="short",
            characters=self.goal.characters,
            global_weights={"priority": 2.0},
            status="active",
        )
        updated = service.update_goal(goal_id, changed)
        self.assertGreaterEqual(updated["invalidated_cache_entries"], 1)
        recomputed = service.recalculate(
            account_snapshot_id=snapshot_id,
            goal_id=goal_id,
        )
        self.assertEqual(recomputed["cache"], "miss")

    def test_concurrent_planning_saves_do_not_lose_rows(self):
        service = PlanningService(self.root)
        goal_id = service.goals.save(self.goal)
        def save(index):
            snapshot = AccountSnapshot(
                source={"kind": "simulation", "reproducible_seed": index},
                characters=[self.character],
                inventory={"artifact_count": 20},
                metadata={"missing_fields": []},
            )
            snapshot_id = service.accounts.save(snapshot, f"并发账号 {index}")
            return service.calculate_and_save(
                account_snapshot_id=snapshot_id,
                goal_id=goal_id,
                name=f"并发方案 {index}",
            )
        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(save, range(12)))
        plan_ids = {item["plan_id"] for item in outcomes}
        self.assertEqual(len(plan_ids), 12)
        self.assertEqual(len(service.accounts.list()), 12)
        self.assertGreaterEqual(len(service.list_plans()), 12)

    def test_backup_create_health_and_restore_preserves_account_and_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            backup_destination = Path(directory) / "backups"
            service = PlanningService(root)
            snapshot_id = service.accounts.save(self.snapshot, "备份前账号")
            goal_id = service.goals.save(self.goal)
            plan = service.calculate_and_save(
                account_snapshot_id=snapshot_id,
                goal_id=goal_id,
                name="备份前方案",
            )
            backup = PersistenceBackup(root)
            health = backup.health()
            self.assertEqual(health["status"], "healthy")
            created = backup.create(backup_destination)
            backup_dir = Path(created["backup_dir"])

            service.accounts.update(
                snapshot_id,
                AccountSnapshot(
                    source={"kind": "manual"},
                    characters=[],
                    inventory={"artifact_count": 0},
                    metadata={"missing_fields": ["restored_data"]},
                ),
                "修改后账号",
            )
            restored = backup.restore(backup_dir, replace=True)
            self.assertIn("accounts.sqlite3", restored["restored"])
            restored_service = PlanningService(root)
            restored_plan = restored_service.plans.get(plan["plan_id"])
            self.assertEqual(restored_plan["name"], "备份前方案")
            self.assertEqual(
                restored_service.accounts.get(snapshot_id)["name"],
                "备份前账号",
            )

    def test_backup_restore_requires_explicit_replace_and_valid_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            destination = Path(directory) / "backups"
            service = PlanningService(root)
            service.accounts.save(self.snapshot, "备份账号")
            created = PersistenceBackup(root).create(destination)
            backup_dir = Path(created["backup_dir"])
            with self.assertRaisesRegex(ValueError, "replace=True"):
                PersistenceBackup(root).restore(backup_dir)
            manifest = backup_dir / "manifest.json"
            content = manifest.read_text(encoding="utf-8")
            manifest.write_text(content.replace('"sha256":', '"checksum":', 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "校验失败"):
                PersistenceBackup(root).restore(backup_dir, replace=True)

    def test_planning_state_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            first = PlanningService(directory)
            snapshot_id = first.accounts.save(self.snapshot, "持久账号")
            goal_id = first.goals.save(self.goal)
            plan = first.calculate_and_save(
                account_snapshot_id=snapshot_id,
                goal_id=goal_id,
                name="重启前方案",
            )
            restarted = PlanningService(directory)
            restored = restarted.plans.get(plan["plan_id"])
            self.assertEqual(restored["name"], "重启前方案")
            self.assertEqual(restarted.accounts.get(snapshot_id)["snapshot"]["characters"][0]["identity"], "Neuvillette")
            self.assertEqual(restarted.goals.get(goal_id)["goal"]["name"], "那维莱特养成")


if __name__ == "__main__":
    unittest.main()
