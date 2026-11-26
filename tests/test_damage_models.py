import unittest
from decimal import Decimal

from artifact_sim.build_objective import character_weapon_panel
from artifact_sim.damage_models import available_models, get_damage_model
from damage.damage_models import evaluate_formula_breakdown


class DamageModelTests(unittest.TestCase):
    def setUp(self):
        self.model = get_damage_model("那维莱特")
        self.stats, _ = character_weapon_panel(
            {"id": "Neuvillette", "level": 90, "ascension": 6},
            {"id": "Tome of the Eternal Flow", "level": 90, "ascension": 6},
            self.model.panel_bonus_stats,
        )

    def test_neuvillette_baseline_matches_pinned_optimizer(self):
        self.assertAlmostEqual(self.model.evaluate(self.stats, set()), 2066.5652717973667, places=6)
        self.assertAlmostEqual(
            self.model.evaluate(self.stats, {"SET_15031_2", "SET_15031_4"}),
            3592.715743156428,
            places=6,
        )
        self.assertIn("HP_PERCENT", self.model.relevant_stats)
        self.assertEqual(self.model.target_four_piece_set, 15031)

    def test_registry_and_scenario_validation(self):
        self.assertIn("neuvillette_charged_judgment", available_models())
        self.assertEqual(get_damage_model("Neuvillette").key, "neuvillette_charged_judgment")
        self.assertGreater(len(available_models()), 100)
        self.assertEqual(get_damage_model("Albedo").key, "albedo_skill_blossom")
        with self.assertRaisesRegex(ValueError, "暂无"):
            get_damage_model("Unknown Character")
        with self.assertRaisesRegex(ValueError, "C0"):
            get_damage_model("那维莱特", constellation=1)
        with self.assertRaisesRegex(ValueError, "专武"):
            get_damage_model("那维莱特", weapon="Dull Blade")

    def test_fast_increases_match_objective(self):
        increases = {
            "HP_PERCENT": Decimal("4.66"),
            "HP": Decimal("239"),
            "CRIT_RATE": Decimal("3.89"),
            "CRIT_DMG": Decimal("7.77"),
        }
        for flags in (set(), {"SET_15031_2", "SET_15031_4"}):
            expected = float(self.model.objective.evaluate(self.stats, flags, increases)) * self.model.scale
            self.assertAlmostEqual(
                self.model.evaluate_increases(self.stats, flags, increases), expected, places=9,
            )

    def test_noncrit_mode_removes_crit_scaling(self):
        model = get_damage_model("那维莱特", crit_mode="noncrit")
        stats, _ = character_weapon_panel(
            {"id": "Neuvillette", "level": 90, "ascension": 6},
            {"id": "Tome of the Eternal Flow", "level": 90, "ascension": 6},
            model.panel_bonus_stats,
        )
        self.assertAlmostEqual(model.evaluate(stats, set()), 1898.8888307122786, places=6)
        self.assertNotIn("CRIT_RATE", model.relevant_stats)

    def test_talent_multiplier_tracks_neuvillette_auto_talent_level(self):
        baseline = get_damage_model("Neuvillette")
        level_one = get_damage_model(
            "Neuvillette",
            talents={"auto": 1, "skill": 9, "burst": 10},
        )
        self.assertAlmostEqual(
            level_one.evaluate(self.stats, set()) / baseline.evaluate(self.stats, set()),
            0.073186 / 0.14467,
            places=9,
        )
        self.assertEqual(level_one.formula_factors["talent"]["level"], 1)

    def test_numeric_breakdown_reconstructs_the_exact_scoring_factors(self):
        flags = {"SET_15031_2", "SET_15031_4"}
        breakdown = evaluate_formula_breakdown(
            self.model.formula_factors,
            self.stats,
            flags,
        )

        self.assertAlmostEqual(
            breakdown["unscaled_score"] * self.model.scale,
            self.model.evaluate(self.stats, flags),
            places=10,
        )
        self.assertEqual(
            len(breakdown["damage_term_values"]),
            len(self.model.formula_factors["damage_terms"]),
        )
        self.assertEqual(breakdown["crit_rate"], 41.0)
        self.assertAlmostEqual(breakdown["damage_multiplier"], 1.69)


if __name__ == "__main__":
    unittest.main()
