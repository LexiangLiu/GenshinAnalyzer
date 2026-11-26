import unittest

from artifact_sim.build_objective import character_weapon_panel
from artifact_sim.character_models import (
    catalog_available, catalog_models, catalog_spec, get_catalog_model, weapon_matches,
)
from artifact_sim.damage_models import get_damage_model
from artifact_sim.game_data import SETS
from core.equipment import set_effects


class CharacterModelCatalogTests(unittest.TestCase):
    def test_catalog_covers_most_of_the_roster(self):
        self.assertGreater(len(catalog_models()), 100)
        self.assertIn("arlecchino_normal_3", catalog_available())
        self.assertIn("hutao_charged", catalog_available())

    def test_every_spec_builds_and_scores_at_base_panel(self):
        for spec in catalog_models():
            with self.subTest(character=spec["character"]):
                model = get_catalog_model(spec["key"])
                weapon = spec.get("weapon")
                stats, _ = character_weapon_panel(
                    {"id": spec["character"], "level": 90, "ascension": 6},
                    None if not weapon else {"id": weapon, "level": 90, "ascension": 6},
                    model.panel_bonus_stats,
                )
                score = model.evaluate(stats, set())
                expected = spec["calibration"]["baseline_average_damage"]
                self.assertAlmostEqual(score, expected, delta=max(1e-7, expected * 1e-8))
                active_flags = model.set_flags
                objective_score = float(model.objective.evaluate(stats, active_flags)) * model.scale
                self.assertAlmostEqual(
                    model.evaluate(stats, active_flags),
                    objective_score,
                    delta=max(1e-7, abs(objective_score) * 1e-10),
                )
                self.assertLessEqual(len(model.expression), 1024)
                target = spec.get("target_four_piece_set")
                if target is not None:
                    self.assertIn(5, SETS[target]["rarities"])
                    self.assertIn("4", SETS[target]["effects"])

    def test_default_models_and_accuracy_metadata(self):
        harbinger = get_damage_model("阿蕾奇诺")
        self.assertEqual(harbinger.key, "arlecchino_normal_3")
        self.assertIn("PYRO_DMG_BONUS", harbinger.relevant_stats)
        self.assertIn("CRIT_RATE", harbinger.relevant_stats)
        self.assertEqual(harbinger.assumptions["calibration"]["accuracy"], "high")
        self.assertIn("sources", harbinger.assumptions)

    def test_noncrit_catalog_model_removes_crit_stats(self):
        model = get_catalog_model("arlecchino_normal_3", "noncrit")
        self.assertNotIn("CRIT_RATE", model.relevant_stats)
        self.assertNotIn("CRIT_DMG", model.relevant_stats)

    def test_fixed_weapon_aliases(self):
        for weapon in ("Staff of Homa", "护摩之杖", "13501", "StaffOfHoma"):
            with self.subTest(weapon=weapon):
                model = get_damage_model("胡桃", weapon=weapon)
                self.assertEqual(model.key, "hutao_charged")
        with self.assertRaisesRegex(ValueError, "只覆盖武器"):
            get_damage_model("胡桃", weapon="White Tassel")
        self.assertTrue(weapon_matches("护摩之杖", "Staff of Homa"))
        self.assertFalse(weapon_matches("护摩", "Staff of Homa"))

    def test_catalog_rejects_unsupported_options(self):
        with self.assertRaisesRegex(ValueError, "不支持参数"):
            get_damage_model("阿蕾奇诺", a1_stacks=3)
        with self.assertRaisesRegex(ValueError, "1 到 10"):
            get_damage_model("阿蕾奇诺", talents={"auto": 11, "skill": 10, "burst": 10})

    def test_talent_level_is_a_dynamic_scoring_input(self):
        spec = catalog_spec("arlecchino_normal_3")
        level_ten = get_catalog_model("arlecchino_normal_3")
        talents = dict(spec["assumptions"]["talents"])
        talents["auto"] = 1
        level_one = get_catalog_model("arlecchino_normal_3", talents=talents)
        stats, _ = character_weapon_panel(
            {"id": spec["character"], "level": 90, "ascension": 6},
            None,
            level_ten.panel_bonus_stats,
        )
        self.assertAlmostEqual(
            level_one.evaluate(stats, set()) / level_ten.evaluate(stats, set()),
            spec["talent_scaling"]["multipliers"]["1"],
            places=9,
        )
        self.assertEqual(level_one.formula_factors["talent"]["level"], 1)

    def test_fixed_set_is_an_external_scoring_input(self):
        spec = catalog_spec("arlecchino_normal_3")
        model = get_catalog_model("arlecchino_normal_3", target_four_piece_set=15001)
        stats, _ = character_weapon_panel(
            {"id": spec["character"], "level": 90, "ascension": 6},
            None,
            model.panel_bonus_stats,
        )
        self.assertEqual(model.target_four_piece_set, 15001)
        self.assertIn("SET_15001_4", model.set_flags)
        baseline = model.evaluate(stats, set())
        with_bonus = model.evaluate(stats, {"SET_15001_2", "SET_15001_4"})
        self.assertGreater(with_bonus, baseline)
        self.assertAlmostEqual(
            with_bonus,
            float(model.objective.evaluate(stats, {"SET_15001_2", "SET_15001_4"})) * model.scale,
            places=7,
        )

    def test_static_set_panel_bonus_comes_from_panel_once(self):
        model = get_catalog_model("arlecchino_normal_3", target_four_piece_set=15001)
        stats, _ = character_weapon_panel(
            {"id": "Arlecchino", "level": 90, "ascension": 6},
            None,
            model.panel_bonus_stats,
        )
        static, _ = set_effects({15001: 2})
        equipped_stats = dict(stats)
        for stat, value in static.items():
            equipped_stats[stat] = equipped_stats.get(stat, 0) + value
        self.assertNotIn("SET_15001_2", model.set_flags)
        without_panel_bonus = model.evaluate(stats, set())
        with_panel_bonus = model.evaluate(equipped_stats, set())
        self.assertGreater(with_panel_bonus, without_panel_bonus)
        self.assertAlmostEqual(
            model.evaluate(equipped_stats, {"SET_15001_2"}),
            with_panel_bonus,
            places=7,
        )

    def test_spec_lookup(self):
        spec = catalog_spec("nahida_karma_dmg")
        self.assertEqual(spec["go_key"], "Nahida")
        self.assertIn("ELEMENTAL_MASTERY", spec["coefficients"])


if __name__ == "__main__":
    unittest.main()
