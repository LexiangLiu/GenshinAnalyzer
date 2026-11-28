import unittest
import tempfile
import json
import re
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import urlopen

from damage.damage_models import DamageModel, get_damage_model
from tools.acceptance_ui import (
    AcceptanceHTTPServer,
    AcceptanceRequestHandler,
    AcceptanceService,
    _public_formula,
    _formula_models_for_member,
    _projection_comparison,
    _public_result,
    _readable_formula,
    build_selected_request,
    format_formula_expression,
    optimizer_roster_size,
    validate_generation,
)
from artifact_sim import Inventory, Simulator
from artifact_sim.__main__ import _party_pieces
from tools.simulate_account import (
    MAIN_ROSTER,
    MAX_ARTIFACT_INVENTORY,
    SLOTS,
    _build_roster,
    available_character_count,
    validate_artifact_count,
)
from data.references import traveler_forms


class ValidateGenerationTests(unittest.TestCase):
    def test_defaults_and_configurable_seed(self):
        self.assertEqual(
            validate_generation({"characters": 3, "artifacts": 80, "seed": 17}),
            {"characters": 3, "artifacts": 80, "seed": 17, "traveler_element": None},
        )
        self.assertEqual(
            validate_generation({}),
            {"characters": 8, "artifacts": 180, "seed": 42, "traveler_element": None},
        )

    def test_artifact_capacity_boundary_is_shared_with_generator(self):
        self.assertEqual(validate_artifact_count(20), 20)
        self.assertEqual(validate_artifact_count(MAX_ARTIFACT_INVENTORY), MAX_ARTIFACT_INVENTORY)
        self.assertEqual(
            validate_generation({"artifacts": MAX_ARTIFACT_INVENTORY})["artifacts"],
            MAX_ARTIFACT_INVENTORY,
        )
        for invalid in (19, MAX_ARTIFACT_INVENTORY + 1, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_artifact_count(invalid)
            with self.subTest(api_invalid=invalid), self.assertRaises(ValueError):
                validate_generation({"artifacts": invalid})

    def test_rejects_out_of_range_generation_config(self):
        roster_limit = available_character_count()
        for payload in (
            {"characters": 0},
            {"characters": 1},
            {"characters": roster_limit + 1},
            {"artifacts": 19},
            {"seed": True},
            [],
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_generation(payload)

    def test_accepts_and_builds_the_complete_offline_roster(self):
        roster_limit = available_character_count()
        config = validate_generation({"characters": roster_limit})
        roster, _ = _build_roster(seed=73, owned_count=config["characters"], invested_count=1)
        self.assertEqual(len(roster), roster_limit)
        self.assertEqual(len({member["character"] for member in roster}), roster_limit)
        travelers = [member for member in roster if member.get("identity") == "traveler"]
        manekins = [member for member in roster if member.get("identity") == "manekin"]
        self.assertEqual(len(travelers), 1)
        self.assertEqual(len(manekins), 1)
        self.assertIn(travelers[0]["form_key"], {form["key"] for form in traveler_forms()})
        self.assertIn(travelers[0]["appearance"], ("aether", "lumine"))
        self.assertIn(manekins[0]["appearance"], ("manekin", "manekina"))
        self.assertEqual(manekins[0]["identity"], "manekin")
        self.assertEqual(manekins[0]["element"], "ELEMENT_ADAPTIVE")
        self.assertEqual(manekins[0]["current_element"], travelers[0]["element"])
        self.assertTrue(manekins[0]["current_element_name"])
        self.assertEqual(manekins[0]["constellation"], 0)
        self.assertFalse(manekins[0]["constellations_available"])
        self.assertEqual(manekins[0]["talents"], {"auto": 10, "skill": 10, "burst": 10})
        self.assertEqual(manekins[0]["ascension_materials"], [])
        self.assertEqual(manekins[0]["mora_cost"], 0)
        self.assertIn(manekins[0]["level"], (90, 95))

    def test_traveler_form_is_seeded_or_explicitly_selected(self):
        explicit = validate_generation({
            "characters": 3, "traveler_element": "anemo", "seed": 73,
        })
        self.assertEqual(
            validate_generation({"traveler_element": "风"})["traveler_element"],
            "anemo",
        )
        self.assertEqual(explicit["traveler_element"], "anemo")
        first, _ = _build_roster(73, 3, 1, explicit["traveler_element"])
        second, _ = _build_roster(73, 3, 1, explicit["traveler_element"])
        traveler, = [member for member in first if member.get("identity") == "traveler"]
        self.assertEqual(traveler, next(member for member in second if member.get("identity") == "traveler"))
        self.assertEqual(traveler["form_key"], "traveleranemo")
        with self.assertRaisesRegex(ValueError, "旅行者元素形态无效"):
            validate_generation({"traveler_element": "unknown"})

    def test_minimum_account_contains_one_each_of_traveler_and_manekin(self):
        minimum = validate_generation({"characters": 3})["characters"]
        self.assertEqual(minimum, 3)
        roster, _ = _build_roster(seed=12, owned_count=minimum, invested_count=1)
        self.assertEqual(len(roster), minimum)
        self.assertEqual(sum(row.get("identity") == "traveler" for row in roster), 1)
        self.assertEqual(sum(row.get("identity") == "manekin" for row in roster), 1)

    def test_browser_input_limit_comes_from_offline_roster(self):
        page = AcceptanceRequestHandler._page_content()
        self.assertIn(
            f'max="{available_character_count()}"'.encode("ascii"),
            page,
        )
        self.assertIn(
            f'max="{MAX_ARTIFACT_INVENTORY}"'.encode("ascii"),
            page,
        )
        self.assertIn("固定四件套".encode("utf-8"), page)
        self.assertIn(b"fixed_four_piece_sets", page)
        self.assertIn("旅行者元素".encode("utf-8"), page)
        self.assertIn("奇偶".encode("utf-8"), page)
        self.assertNotIn(b"__TRAVELER_ELEMENT_OPTIONS__", page)

    def test_optimizer_roster_scales_with_inventory_without_capping_owned_roster(self):
        roster_limit = available_character_count()
        per_character_inventory = len(MAIN_ROSTER) * len(SLOTS)
        self.assertEqual(optimizer_roster_size(roster_limit, 20), 1)
        self.assertEqual(
            optimizer_roster_size(roster_limit, 180),
            min(roster_limit - 2, len(MAIN_ROSTER), 180 // per_character_inventory),
        )
        self.assertEqual(
            optimizer_roster_size(roster_limit, 2000),
            min(roster_limit - 2, len(MAIN_ROSTER), 2000 // per_character_inventory),
        )


class BuildSelectedRequestTests(unittest.TestCase):
    def setUp(self):
        self.request = {
            "members": [
                {"character": "A", "name": "A", "target_four_piece_set": 15031},
                {"character": "B", "name": "B", "target_four_piece_set": 15035},
                {"character": "C", "name": "C", "target_four_piece_set": 15020},
            ],
            "domains": [101, 202],
            "max_nodes": 50000,
        }

    def test_single_member_request_preserves_domain_and_sets_seed_budget(self):
        result = build_selected_request(self.request, ["B"], "single", 73, 12000)
        self.assertEqual([member["character"] for member in result["members"]], ["B"])
        self.assertEqual(result["domains"], [101, 202])
        self.assertEqual(result["seed"], 73)
        self.assertEqual(result["max_nodes"], 12000)
        self.assertEqual(len(self.request["members"]), 3)

    def test_multi_member_request_filters_domains(self):
        result = build_selected_request(self.request, ["A", "C"], "multi", 0, 2000, [202])
        self.assertEqual([member["character"] for member in result["members"]], ["A", "C"])
        self.assertEqual(result["domains"], [202])

    def test_default_fixed_set_selection_preserves_existing_member_rules(self):
        result = build_selected_request(self.request, ["B"], "single", 0, 1000)
        self.assertEqual(
            result["members"],
            [{"character": "B", "name": "B", "target_four_piece_set": 15035}],
        )
        self.assertNotIn("fixed_four_piece_set", result["members"][0])

    def test_single_member_can_override_its_fixed_four_piece_set(self):
        result = build_selected_request(
            self.request, ["B"], "single", 0, 1000,
            fixed_four_piece_sets={"B": 15031},
        )
        self.assertEqual(result["members"][0]["fixed_four_piece_set"], 15031)
        self.assertNotIn("fixed_four_piece_set", self.request["members"][1])

    def test_multi_member_can_use_distinct_fixed_four_piece_sets(self):
        result = build_selected_request(
            self.request, ["A", "C"], "multi", 0, 1000,
            fixed_four_piece_sets={"A": 15031, "C": 15035},
        )
        self.assertEqual(
            [member["fixed_four_piece_set"] for member in result["members"]],
            [15031, 15035],
        )

    def test_formula_objectives_normalize_weights_per_character(self):
        registry = {
            key: {"character": character}
            for key, character in (
                ("a_normal", "A"), ("a_skill", "A"),
                ("b_skill", "B"), ("b_burst", "B"),
            )
        }
        choices = {
            "A": [{"key": "a_normal"}, {"key": "a_skill"}],
            "B": [{"key": "b_skill"}, {"key": "b_burst"}],
        }
        objectives = {
            "A": {
                "mode": "relative",
                "formulas": [
                    {"key": "a_normal", "weight": 3},
                    {"key": "a_skill", "weight": 1},
                ],
            },
            "B": {
                "mode": "raw",
                "formulas": [
                    {"key": "b_skill", "weight": 1},
                    {"key": "b_burst", "weight": 1},
                ],
            },
        }
        with (
            patch("tools.acceptance_ui.available_models", return_value=registry),
            patch(
                "tools.acceptance_ui._formula_choices",
                side_effect=lambda character, member: choices[character],
            ),
            patch("tools.acceptance_ui.catalog_formula_selectable", return_value=True),
        ):
            result = build_selected_request(
                self.request,
                ["A", "B"],
                "multi",
                17,
                1000,
                formula_objectives=objectives,
            )

        by_character = {member["character"]: member["formula_objective"] for member in result["members"]}
        self.assertEqual(by_character["A"]["mode"], "relative")
        self.assertEqual(by_character["A"]["formulas"][0]["weight"], 0.75)
        self.assertEqual(by_character["A"]["formulas"][1]["weight"], 0.25)
        self.assertEqual(by_character["B"]["mode"], "raw")
        self.assertEqual([row["weight"] for row in by_character["B"]["formulas"]], [0.5, 0.5])

    def test_composite_objective_rejects_uncalibrated_formula(self):
        registry = {"a_low": {"character": "A"}, "a_good": {"character": "A"}}
        with (
            patch("tools.acceptance_ui.available_models", return_value=registry),
            patch(
                "tools.acceptance_ui._formula_choices",
                return_value=[{"key": "a_low"}, {"key": "a_good"}],
            ),
            patch(
                "tools.acceptance_ui.catalog_formula_selectable",
                side_effect=lambda key: key == "a_good",
            ),
            self.assertRaisesRegex(ValueError, "未达到可综合评分"),
        ):
            build_selected_request(
                self.request,
                ["A"],
                "single",
                0,
                1000,
                formula_objectives={
                    "A": {
                        "mode": "raw",
                        "formulas": [
                            {"key": "a_low", "weight": 1},
                            {"key": "a_good", "weight": 1},
                        ],
                    },
                },
            )

    def test_rejects_invalid_selection_and_search_budget(self):
        invalid = (
            ([], "single", 0, 1000, None),
            (["A", "B"], "single", 0, 1000, None),
            (["A"], "multi", 0, 1000, None),
            (["missing"], "single", 0, 1000, None),
            (["A"], "single", 0, 999, None),
            (["A"], "single", 0, 1000, [999]),
            (["A", "A"], "multi", 0, 1000, None),
        )
        for selected, mode, seed, nodes, domains in invalid:
            with self.subTest(selected=selected, mode=mode, nodes=nodes), self.assertRaises(ValueError):
                build_selected_request(self.request, selected, mode, seed, nodes, domains)
        for fixed_sets in (
            {"A": 999999},
            {"B": True},
            {"unselected": 15031},
        ):
            with self.subTest(fixed_sets=fixed_sets), self.assertRaises(ValueError):
                build_selected_request(
                    self.request, ["A"], "single", 0, 1000,
                    fixed_four_piece_sets=fixed_sets,
                )

    def test_selected_request_rejects_multiple_traveler_forms(self):
        self.request["members"].extend([
            {"character": "Traveler (Anemo)", "name": "旅行者·风", "target_four_piece_set": 15032},
            {"character": "旅行者·岩", "name": "旅行者·岩", "target_four_piece_set": 15032},
        ])
        with self.assertRaisesRegex(ValueError, "一个队伍只能包含一个旅行者身份"):
            build_selected_request(
                self.request,
                ["Traveler (Anemo)", "旅行者·岩"],
                "multi",
                0,
                1000,
            )

    def test_selected_request_rejects_duplicate_manekin_appearances_and_uncalibrated_scoring(self):
        with self.assertRaisesRegex(ValueError, "一个队伍只能包含一个奇偶身份"):
            build_selected_request(
               self.request, ["Manekin", "Manekina"], "multi", 0, 1000,
            )
        self.request["members"].append(
            {"character": "Manekin", "name": "奇偶", "target_four_piece_set": None},
        )
        with self.assertRaisesRegex(ValueError, "奇偶战斗模型尚未校准"):
            build_selected_request(
               self.request, ["Manekin"], "single", 0, 1000,
            )


class ProjectionIntegrationTests(unittest.TestCase):
    def test_formula_catalog_lists_multiple_models_and_marks_the_scoring_model(self):
        selected = DamageModel(
            key="demo_primary",
            character="Demo",
            label="Primary hit",
            expression="ATK",
            scale=2,
            assumptions={"conditions": {"stacks": 2}, "sources": ["https://example.test/source"]},
        )
        reference = DamageModel(
            key="demo_reference",
            character="Demo",
            label="Reference hit",
            expression="ATK + HP",
        )

        with (
            patch(
                "tools.acceptance_ui.available_models",
                return_value={
                    "demo_primary": {"character": "Demo"},
                    "demo_reference": {"character": "Demo"},
                },
            ),
            patch(
                "tools.acceptance_ui.get_damage_model",
                side_effect=lambda character, key, **options: (
                    selected if key == "demo_primary" else reference
                ),
            ),
            patch(
                "tools.acceptance_ui.list_damage_formulas",
                return_value={
                    "attacks": [
                        {"name": "normal_hit", "category": "auto"},
                        {"name": "burst_hit", "category": "burst"},
                    ],
                    "conditions": {"stacks": {"min": 0, "max": 2}},
                },
            ),
            patch(
                "tools.acceptance_ui.damage_engine_info",
                return_value={"upstream": "Pinned upstream formula catalog"},
            ),
        ):
            formulas = _formula_models_for_member(
                {"character": "Demo", "model": "demo_primary"},
                {"character": "Demo"},
            )

        self.assertEqual(len(formulas), 4)
        active, = [formula for formula in formulas if formula["selected"]]
        self.assertEqual(active["key"], "demo_primary")
        self.assertIn("* 2", active["expression"])
        self.assertEqual(active["assumptions"]["conditions"], {"stacks": 2})
        self.assertEqual(active["sources"], ["https://example.test/source"])
        self.assertEqual(
            {formula["key"] for formula in formulas if formula["formula_type"] == "registered_model"},
            {"demo_primary", "demo_reference"},
        )
        upstream, = [
            formula for formula in formulas if formula["key"] == "upstream:normal_hit"
        ]
        self.assertIsNone(upstream["expression"])
        self.assertIn("不提供", upstream["expression_note"])
        self.assertEqual(upstream["sources"], ["Pinned upstream formula catalog"])

    def test_formula_metadata_is_attached_to_each_party_member(self):
        models = {
            "first_model": DamageModel(
                key="first_model", character="First", label="First formula", expression="ATK",
            ),
            "second_model": DamageModel(
                key="second_model", character="Second", label="Second formula", expression="HP",
            ),
        }
        registry = {
            key: {"character": model.character}
            for key, model in models.items()
        }

        with (
            patch("tools.acceptance_ui.available_models", return_value=registry),
            patch(
                "tools.acceptance_ui.get_damage_model",
                side_effect=lambda character, key, **options: models[key],
            ),
        ):
            result = _public_result(
                {"members": [
                    {"character": "First", "model": "first_model"},
                    {"character": "Second", "model": "second_model"},
                ]},
                {"members": [
                    {"character": "First"},
                    {"character": "Second"},
                ]},
            )

        self.assertEqual(len(result["members"]), 2)
        for member in result["members"]:
            formula, = member["formula_models"]
            self.assertTrue(formula["selected"])
            self.assertEqual(formula["key"], member["model"])

    def test_details_are_paginated_searchable_and_show_equipment_relations(self):
        service = AcceptanceService()
        self.addCleanup(service.close)
        generated = service.generate({"characters": 30, "artifacts": 30, "seed": 91})
        session_id = generated["session_id"]

        characters = service.details(session_id, "characters")
        self.assertEqual(characters["total"], 30)
        self.assertEqual(characters["page_size"], 24)
        self.assertEqual(len(characters["items"]), 24)
        self.assertEqual(service.details(session_id, "characters", page=2)["total"], 30)
        character = characters["items"][0]
        self.assertIn("等级", {field["label"] for field in character["fields"]})
        filtered = service.details(session_id, "characters", character["title"])
        self.assertEqual(filtered["total"], 1)

        weapons = service.details(session_id, "weapons")
        self.assertGreater(weapons["total"], 30)
        self.assertTrue(any(
            field["label"] == "装备角色" and field["value"] != "未装备"
            for item in weapons["items"] for field in item["fields"]
        ))
        self.assertTrue(any(
            field["label"] == "基础攻击力（图鉴原始值）"
            for item in weapons["items"] for field in item["fields"]
        ))

        first_artifact_page = service.details(session_id, "artifacts")
        second_artifact_page = service.details(session_id, "artifacts", page=2)
        self.assertEqual(first_artifact_page["total"], 30)
        self.assertEqual(len(first_artifact_page["items"]), 24)
        self.assertEqual(len(second_artifact_page["items"]), 6)
        equipped = next(
            item for item in first_artifact_page["items"] + second_artifact_page["items"]
            if any(field["label"] == "当前装备角色" and field["value"] != "未装备"
                   for field in item["fields"])
        )
        artifact = service.details(
            session_id, "artifacts", item_id=equipped["id"],
        )["item"]
        artifact_fields = {field["label"] for field in artifact["fields"]}
        self.assertTrue({
            "套装", "部位", "星级", "等级", "主词条", "副词条", "当前装备角色",
        }.issubset(artifact_fields))

        server = AcceptanceHTTPServer(("127.0.0.1", 0), service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop_server():
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.addCleanup(stop_server)
        query = urlencode({"kind": "artifacts", "session_id": session_id, "page": "2"})
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/details?{query}") as response:
            page = json.loads(response.read().decode("utf-8"))
        self.assertEqual(len(page["items"]), 6)

    def test_party_inventory_can_be_loaded_as_expected_level_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "inventory.sqlite3"
            inventory = Inventory(db)
            artifact = Simulator(17).create(
                15031,
                slot="FLOWER",
                main_stat="HP",
                initial_count=3,
                substats=[("CRIT_RATE", 1), ("CRIT_DMG", 2), ("ATK_PERCENT", 3)],
            )
            artifact_id, = inventory.add([artifact])

            actual, = _party_pieces({}, db)
            projected, = _party_pieces({"expected_full_level": True}, db)

            self.assertIsNone(actual.expected_full_level_substats)
            self.assertIsNotNone(projected.expected_full_level_substats)
            self.assertEqual(projected.artifact_id, artifact_id)

    def test_projection_comparison_identifies_replacement_and_target_change(self):
        current = {
            "total_objective": 1.0,
            "members": [{
                "character": "Neuvillette",
                "loadout": {
                    "damage": 100.0,
                    "pieces": [{"slot": "FLOWER", "id": "current", "piece_name": "旧花"}],
                },
            }],
        }
        projected = {
            "total_objective": 1.12,
            "members": [{
                "character": "Neuvillette",
                "name": "那维莱特",
                "loadout": {
                    "damage": 112.0,
                    "pieces": [{
                        "slot": "FLOWER",
                        "id": "candidate",
                        "piece_name": "潜力花",
                        "set_name": "逐影猎人",
                        "state": {"level": 8},
                        "expected_full_level": {
                            "level": 20,
                            "main_value": 4780,
                            "substats": {"CRIT_RATE": 22.4},
                        },
                    }],
                },
            }],
        }
        comparison = _projection_comparison(current, projected)

        self.assertAlmostEqual(comparison["objective_delta"], 0.12)
        self.assertAlmostEqual(comparison["weighted_damage_delta_pct"], 12.0)
        member, = comparison["members"]
        self.assertAlmostEqual(member["damage_delta_pct"], 12.0)
        candidate, = member["candidates"]
        self.assertTrue(candidate["replaces_current_best"])
        self.assertEqual(candidate["current_best_artifact_id"], "current")
        self.assertEqual(candidate["level"], 8)
        self.assertEqual(candidate["expected_full_level"]["level"], 20)
        self.assertIn("期望属性估算", comparison["semantics"])

    def test_service_returns_current_and_projected_results_separately(self):
        service = AcceptanceService()
        self.addCleanup(service.close)
        generated = service.generate({"characters": 3, "artifacts": 20, "seed": 73})
        traveler, = [
            row for row in generated["account"]["roster"]
            if row.get("identity") == "traveler"
        ]
        self.assertIn(traveler["form_key"], {form["key"] for form in traveler_forms()})
        traveler_detail = service.details(
            generated["session_id"], "characters", query="Traveler",
        )["items"]
        self.assertEqual(len(traveler_detail), 1)
        self.assertIn("当前形态天赋图鉴", {field["label"] for field in traveler_detail[0]["fields"]})
        manekin, = [
            row for row in generated["account"]["roster"]
            if row.get("identity") == "manekin"
        ]
        self.assertNotIn(
            manekin["character"],
            {row["character"] for row in generated["account"]["optimizable_roster"]},
        )
        manekin_detail, = service.details(
            generated["session_id"], "characters", query="Manekin",
        )["items"]
        manekin_fields = {field["label"]: field["value"] for field in manekin_detail["fields"]}
        self.assertEqual(manekin_fields["逻辑身份"], "奇偶（Manekin / Manekina 共用）")
        self.assertEqual(manekin_fields["当前元素"], manekin["current_element_name"])
        self.assertTrue(manekin_fields["能力摘要"])
        self.assertIn("普攻 Lv10", manekin_fields["技能倍率与参数（当前天赋等级）"])
        self.assertIn("583.2%", manekin_fields["技能倍率与参数（当前天赋等级）"])
        self.assertIn("未校准", manekin_fields["战斗模型状态"])
        self.assertTrue(any("不参与配装评分" in note for note in manekin_detail["notes"]))
        character = generated["account"]["optimizable_roster"][0]["character"]
        default_set = generated["account"]["optimizable_roster"][0]["model_target_four_piece_set"]
        self.assertTrue(generated["account"]["available_fixed_four_piece_sets"])
        optimized = service.optimize({
            "session_id": generated["session_id"],
            "selected_characters": [character],
            "fixed_four_piece_sets": {character: default_set},
            "mode": "single",
            "seed": 73,
            "max_nodes": 1000,
        })

        self.assertEqual(
            service._session["optimization"]["request"]["members"][0]["fixed_four_piece_set"],
            default_set,
        )
        selected_piece = optimized["result"]["members"][0]["pieces"][0]
        formulas = optimized["result"]["members"][0]["formula_models"]
        active_formula, = [formula for formula in formulas if formula["selected"]]
        self.assertEqual(active_formula["key"], optimized["result"]["members"][0]["model"])
        self.assertTrue(active_formula["expression"])
        self.assertTrue(active_formula["display_expression"])
        numeric_breakdown = active_formula["numeric_breakdown"]
        self.assertTrue(numeric_breakdown["available"], numeric_breakdown.get("message"))
        self.assertAlmostEqual(
            numeric_breakdown["score"],
            optimized["result"]["members"][0]["damage"],
            places=8,
        )
        self.assertIn("伤害基数", [row["label"] for row in numeric_breakdown["rows"]])
        self.assertIn("伤害加成系数", [row["label"] for row in numeric_breakdown["rows"]])
        self.assertEqual(
            numeric_breakdown["multipliers"][0],
            numeric_breakdown["rows"][0]["value"],
        )
        self.assertIn("单个公式", optimized["result"]["members"][0]["formula_semantics"])
        selected_detail = service.details(
            generated["session_id"], "artifacts", item_id=selected_piece["id"],
        )["item"]
        self.assertEqual(selected_detail["id"], selected_piece["id"])
        self.assertIn("members", optimized["result"])
        self.assertIn("result", optimized["projection"])
        self.assertIn("comparison", optimized["projection"])
        self.assertIn("期望属性估算", optimized["projection"]["comparison"]["semantics"])
        self.assertIsNotNone(
            optimized["projection"]["comparison"]["weighted_damage_delta_pct"],
        )
        self.assertTrue(any(
            member["candidates"]
            for member in optimized["projection"]["comparison"]["members"]
        ))
        self.assertIsNone(service._session["optimization"]["result"]["members"][0]["loadout"]["pieces"][0].get("expected_full_level"))
        combined = service.optimize({
            "session_id": generated["session_id"],
            "selected_characters": [character],
            "fixed_four_piece_sets": {character: default_set},
            "formula_objectives": {
                character: {
                    "mode": "relative",
                    "formulas": [
                        {"key": "neuvillette_normal_2", "weight": 3},
                        {"key": "neuvillette_skill", "weight": 1},
                    ],
                },
            },
            "mode": "single",
            "seed": 73,
            "max_nodes": 1000,
        })
        combined_member, = combined["result"]["members"]
        objective = combined_member["formula_objective"]
        self.assertEqual(objective["mode"], "relative")
        self.assertEqual(len(objective["components"]), 2)
        self.assertAlmostEqual(
            sum(component["weight"] for component in objective["components"]), 1,
        )
        self.assertAlmostEqual(
            sum(component["contribution"] for component in objective["components"]),
            objective["score"],
        )
        self.assertEqual(
            {formula["key"] for formula in combined_member["formula_models"] if formula["selected"]},
            {"neuvillette_normal_2", "neuvillette_skill"},
        )

    def test_service_can_optimize_the_single_generated_traveler_form(self):
        service = AcceptanceService()
        self.addCleanup(service.close)
        generated = service.generate({"characters": 3, "artifacts": 20, "seed": 91})
        traveler, = [
            row for row in generated["account"]["optimizable_roster"]
            if row.get("identity") == "traveler"
        ]
        optimized = service.optimize({
            "session_id": generated["session_id"],
            "selected_characters": [traveler["character"]],
            "mode": "single",
            "seed": 91,
            "max_nodes": 1000,
        })
        result, = optimized["result"]["members"]
        self.assertEqual(result["name"], traveler["name_zh"])
        self.assertTrue(result["model"].startswith(traveler["form_key"]))


class FormulaPresentationTests(unittest.TestCase):
    def test_resin_expectation_controls_show_only_aggregate_results(self):
        html_path = Path(__file__).resolve().parents[1] / "tools" / "acceptance_ui.html"
        html = html_path.read_text(encoding="utf-8")
        for control in (
            'id="expectation-domain"',
            'id="expectation-start"',
            'id="expectation-budgets"',
            'id="expectation-samples"',
            'id="expectation-button"',
            'id="expectation-results"',
        ):
            with self.subTest(control=control):
                self.assertIn(control, html)
        self.assertIn("0,200,600,1200", html)
        self.assertIn("只展示统计期望，不展示抽样装备", html)
        self.assertIn("满足固定四件套", html)
        self.assertIn("空库存（无圣遗物约束）", html)
        self.assertIn("state.expectationPending", html)
        self.assertIn('post("/api/expectations"', html)

    def test_account_farming_controls_use_shared_inventory_and_render_risk_breakdown(self):
        html_path = Path(__file__).resolve().parents[1] / "tools" / "acceptance_ui.html"
        html = html_path.read_text(encoding="utf-8")
        for control in (
            'id="account-farming-domain-list"',
            'id="account-farming-start"',
            'id="account-farming-budgets"',
            'id="account-farming-samples"',
            'id="account-farming-target"',
            'id="account-farming-button"',
            'id="account-farming-results"',
        ):
            with self.subTest(control=control):
                self.assertIn(control, html)
        self.assertIn('post("/api/account-farming"', html)
        self.assertIn("selectedCount > 8", html)
        self.assertIn("共享库存上联合重配", html)
        self.assertIn("Wilson 95%", html)
        self.assertIn("P10–P90", html)
        self.assertIn("panel_delta", html)
        self.assertIn("account_gain_contribution_pct", html)

    def test_long_expression_wraps_products_without_changing_formula_tokens(self):
        source = (
            "(ATK + 0.25 * ELEMENTAL_MASTERY) * "
            "(1 + CRIT_RATE * CRIT_DMG / 10000) * "
            "(1 + 15 * SET_15031_2) * 0.072335"
        )
        formatted = format_formula_expression(source)

        self.assertGreaterEqual(formatted.count("\n"), 3)
        self.assertEqual("".join(formatted.split()), "".join(source.split()))

    def test_formula_layout_has_narrow_viewport_wrapping_and_secondary_metadata(self):
        html_path = Path(__file__).resolve().parents[1] / "tools" / "acceptance_ui.html"
        html = html_path.read_text(encoding="utf-8")
        mobile = re.search(
            r"@media \(max-width: 600px\)\s*\{(.*?)\n\s*\}",
            html,
            flags=re.DOTALL,
        )

        self.assertIsNotNone(mobile)
        self.assertIn(".formula-expression", mobile.group(1))
        self.assertIn("max-width: 100%", mobile.group(1))
        self.assertIn(".formula-inputs", html)
        self.assertIn('metadataBlock.className = "formula-metadata"', html)
        self.assertIn('card.className = `formula-card${formula.selected ? " scoring" : ""}`', html)
        self.assertIn("displayedExpression.split(\"\\n\")", html)
        self.assertIn(".formula-line", html)
        self.assertIn(".formula-term", html)
        self.assertIn("selectedFormulas[0]?.label", html)
        self.assertNotIn("textContent = text(formula.key)", html)
        self.assertNotIn("text(member.model)", html)
        self.assertNotIn("JSON.stringify(metadata, null, 2)", html)

    def test_selected_model_uses_chinese_factors_and_actual_dynamic_inputs(self):
        member = {
            "character": "Arlecchino",
            "model": "arlecchino_normal_3",
            "loadout": {
                "final_panel": {
                    "ATK": 2345,
                    "PYRO_DMG_BONUS": 46.6,
                    "CRIT_RATE": 70,
                    "CRIT_DMG": 180,
                },
                "active_set_bonuses": [
                    {"name": "Gladiator's Finale", "pieces": 4, "effect": "Normal Attack DMG +35%"},
                ],
            },
        }
        request_member = {
            "character": "Arlecchino",
            "model": "arlecchino_normal_3",
            "target_four_piece_set": 15001,
            "talents": {"auto": 8, "skill": 7, "burst": 6},
        }
        with (
            patch(
                "tools.acceptance_ui.list_damage_formulas",
                return_value={"attacks": [], "conditions": {}},
            ),
            patch(
                "tools.acceptance_ui.damage_engine_info",
                return_value={"upstream": "Pinned upstream formula catalog"},
            ),
        ):
            formulas = _formula_models_for_member(member, request_member)

        selected, = [formula for formula in formulas if formula["selected"]]
        self.assertIn("攻击力", selected["display_expression"])
        self.assertIn("普通攻击倍率（天赋等级 8", selected["display_expression"])
        self.assertNotRegex(
            selected["display_expression"],
            r"\b(?:HP|ATK|DEF|CRIT_RATE|CRIT_DMG|HYDRO_DMG_BONUS|SET_\d+)\b",
        )
        self.assertEqual(selected["actual_inputs"]["talents"]["auto"], 8)
        self.assertEqual(selected["actual_inputs"]["final_panel"]["ATK"], 2345)
        self.assertEqual(selected["actual_inputs"]["active_set_bonuses"][0]["pieces"], 4)
        self.assertIn("其他未建模套装效果不计入评分", selected["external_inputs"]["set_effects_note"])
        self.assertIn("未纳入", selected["external_inputs"]["weapon_passive"])

    def test_formula_display_hides_internal_tokens_and_uses_chinese_factor_rows(self):
        model = get_damage_model("Neuvillette")
        formula = _public_formula(model, selected=True)
        display = "\n".join(
            [formula["label"], formula["display_expression"], *formula["metadata_summary"]]
        )

        self.assertIn("那维莱特", formula["label"])
        self.assertIn("生命值", formula["display_expression"])
        self.assertIn("逐影猎人2件套伤害加成 +15%", formula["display_expression"])
        self.assertIn("万世流涌大典被动伤害加成 +24%", formula["display_expression"])
        self.assertIn("普通攻击倍率（天赋等级 10", formula["display_expression"])
        self.assertGreaterEqual(formula["display_expression"].count("\n"), 4)
        self.assertNotRegex(
            display,
            r"\b(?:HP|ATK|DEF|CRIT_RATE|CRIT_DMG|HYDRO_DMG_BONUS|SET_\d+|set_id|"
            r"neuvillette_[a-z0-9_]+|charged_judgment)\b",
        )
        self.assertTrue(all("来源：" in entry or "条件：" in entry for entry in formula["metadata_summary"]))
        self.assertNotIn("Tome of the Eternal Flow", display)

    def test_unknown_set_name_is_never_replaced_with_an_internal_id(self):
        model = DamageModel(
            key="unknown_set",
            character="Demo",
            label="演示模型",
            expression="ATK",
            formula_factors={
                "scaling_terms": [{"kind": "stat", "stat": "ATK"}],
                "damage_terms": [{
                    "kind": "set_percent", "value": 20, "set_id": 99999, "pieces": 2,
                }],
            },
        )
        expression = _readable_formula(model)

        self.assertIn("未命名效果（2件套）", expression)
        self.assertNotIn("99999", expression)
        self.assertNotIn("SET_", expression)


if __name__ == "__main__":
    unittest.main()
