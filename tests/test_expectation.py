import unittest

from artifact_sim import Simulator
from artifact_sim.damage_models import get_damage_model
from optimization.loadout_optimizer import LoadoutPiece
from planning.expectation import estimate_loadout_expectations


_BUILD = {
    "FLOWER": ("HP", ("CRIT_RATE", "CRIT_DMG", "ATK_PERCENT", "ENERGY_RECHARGE")),
    "PLUME": ("ATK", ("CRIT_RATE", "CRIT_DMG", "ATK_PERCENT", "HP_PERCENT")),
    "SANDS": ("ATK_PERCENT", ("CRIT_RATE", "CRIT_DMG", "ATK", "ENERGY_RECHARGE")),
    "GOBLET": ("PYRO_DMG_BONUS", ("CRIT_RATE", "CRIT_DMG", "ATK_PERCENT", "ATK")),
    "CIRCLET": ("CRIT_RATE", ("CRIT_DMG", "ATK_PERCENT", "ATK", "ENERGY_RECHARGE")),
}


def _starting_pieces(seed=17):
    simulator = Simulator(seed)
    return [
        LoadoutPiece(simulator.enhance(
            simulator.create(
                15035,
                slot=slot,
                main_stat=main,
                initial_count=4,
                substats=[(stat, 4) for stat in substats],
            ),
            20,
        ))
        for slot, (main, substats) in _BUILD.items()
    ]


class LoadoutExpectationTests(unittest.TestCase):
    def setUp(self):
        self.model = get_damage_model("Arlecchino")
        self.character = {"id": "Arlecchino", "level": 90, "ascension": 6}
        self.weapon = None

    def test_zero_budget_matches_the_selected_starting_inventory(self):
        result = estimate_loadout_expectations(
            [],
            model=self.model,
            character=self.character,
            weapon=self.weapon,
            bonus_stats=dict(self.model.panel_bonus_stats),
            domain=1151,
            resin_budgets=[0],
            starting_inventory="zero",
            sample_count=8,
            seed=41,
        )
        tier, = result["tiers"]
        self.assertEqual(tier["resin"], 0)
        self.assertEqual(tier["samples"], 8)
        self.assertEqual(tier["objective"]["mean"], result["baseline"]["objective"])
        self.assertEqual(tier["objective_delta"]["mean"], 0)
        self.assertTrue(all(row["mean"] == 0 for row in tier["panel_delta"].values()))
        self.assertEqual(tier["search"]["exact_fraction"], 1)

    def test_expected_future_loadouts_report_panel_and_formula_changes(self):
        result = estimate_loadout_expectations(
            _starting_pieces(),
            model=self.model,
            character=self.character,
            weapon=self.weapon,
            bonus_stats=dict(self.model.panel_bonus_stats),
            domain=1151,
            resin_budgets=[0, 20],
            starting_inventory="inventory",
            sample_count=8,
            seed=73,
        )
        self.assertEqual(result["method"]["kind"], "monte_carlo_expected_optimum")
        self.assertEqual(result["method"]["resin_per_claim"], 20)
        tier = result["tiers"][1]
        self.assertEqual(tier["claims"], 1)
        self.assertEqual(tier["samples"], 8)
        self.assertIn("ATK", tier["panel"])
        self.assertIn("ATK", tier["panel_delta"])
        self.assertIn(self.model.key, tier["formulas"])
        self.assertIn("ci95", tier["objective"])
        self.assertIn("delta", tier["formulas"][self.model.key])
        self.assertNotIn("sample_results", result)
        self.assertNotIn("pieces", tier)

    def test_required_four_piece_constraint_reports_unattainable_zero_start(self):
        result = estimate_loadout_expectations(
            [],
            model=self.model,
            character=self.character,
            weapon=self.weapon,
            bonus_stats=dict(self.model.panel_bonus_stats),
            domain=1151,
            resin_budgets=[0],
            starting_inventory="zero",
            sample_count=8,
            seed=19,
            required_four_piece_set=15035,
        )
        tier, = result["tiers"]
        self.assertEqual(tier["feasible_fraction"], 0)
        self.assertEqual(tier["feasible_samples"], 0)
        self.assertIsNone(tier["objective"]["mean"])
        self.assertEqual(result["baseline"]["search"]["required_four_piece_set"], None)

    def test_rejects_unreasonable_or_ambiguous_budgets(self):
        kwargs = {
            "model": self.model,
            "character": self.character,
            "weapon": self.weapon,
            "bonus_stats": dict(self.model.panel_bonus_stats),
            "domain": 1151,
            "sample_count": 8,
        }
        for budgets in ([30], [20, 20], [], [4020]):
            with self.subTest(budgets=budgets), self.assertRaises(ValueError):
                estimate_loadout_expectations([], resin_budgets=budgets, **kwargs)


if __name__ == "__main__":
    unittest.main()
