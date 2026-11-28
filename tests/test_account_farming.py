import unittest
from math import isclose

from core.simulation import Simulator
from damage.damage_models import DamageModel
from data.game_data import DOMAINS
from optimization.loadout_optimizer import LoadoutPiece
from optimization.party_optimizer import PartyMember
from planning.account_farming import estimate_account_farming_priorities


_SLOTS = {
    "FLOWER": ("HP", ("CRIT_RATE", "CRIT_DMG", "ATK_PERCENT", "ENERGY_RECHARGE")),
    "PLUME": ("ATK", ("CRIT_RATE", "CRIT_DMG", "ATK_PERCENT", "HP_PERCENT")),
    "SANDS": ("ATK_PERCENT", ("CRIT_RATE", "CRIT_DMG", "ATK", "ENERGY_RECHARGE")),
    "GOBLET": ("PYRO_DMG_BONUS", ("CRIT_RATE", "CRIT_DMG", "ATK_PERCENT", "ATK")),
    "CIRCLET": ("CRIT_RATE", ("CRIT_DMG", "ATK_PERCENT", "ATK", "ENERGY_RECHARGE")),
}


def _members():
    return [
        PartyMember(
            model=DamageModel(
                key="test_neuvillette",
                character="Neuvillette",
                label="那维莱特测试",
                expression="ATK + HP / 1000",
                monotone=True,
                target_four_piece_set=15035,
            ),
            character={"id": "Neuvillette", "level": 90, "ascension": 6},
            name="那维莱特",
        ),
        PartyMember(
            model=DamageModel(
                key="test_arlecchino",
                character="Arlecchino",
                label="阿蕾奇诺测试",
                expression="ATK + HP / 1000",
                monotone=True,
                target_four_piece_set=15035,
            ),
            character={"id": "Arlecchino", "level": 90, "ascension": 6},
            name="阿蕾奇诺",
        ),
    ]


def _two_loadouts_each(set_id=15035):
    sim = Simulator(73)
    pieces = []
    for slot, (main, substats) in _SLOTS.items():
        for _ in range(2):
            artifact = sim.create(
                set_id,
                slot=slot,
                main_stat=main,
                initial_count=4,
                substats=[(stat, 4) for stat in substats],
            )
            pieces.append(LoadoutPiece(sim.enhance(artifact, 20)))
    return pieces


class AccountFarmingTests(unittest.TestCase):
    def test_zero_start_reports_account_risk_attributes_and_soft_set_completion(self):
        result = estimate_account_farming_priorities(
            [],
            _members(),
            domains=[1151],
            resin_budgets=[20, 40],
            starting_inventory="zero",
            sample_count=8,
            target_gain_pct=1,
            seed=19,
        )

        self.assertEqual(result["baseline"]["account_score"], 1)
        self.assertEqual(result["starting_inventory"], "zero")
        self.assertEqual(result["method"]["searches"], 16)
        self.assertEqual(result["method"]["total_searches"], 17)
        self.assertEqual(result["domains"][0]["tiers"][0]["samples"], 8)
        tier = result["domains"][0]["tiers"][0]
        self.assertGreater(tier["expected_account_score"]["mean"], 0)
        self.assertTrue(isclose(
            tier["gain_per_100_resin_pct"]["mean"],
            tier["expected_gain_pct"]["mean"] * 5,
            rel_tol=1e-10,
            abs_tol=1e-10,
        ))
        self.assertIn("expected_gain_pct", tier)
        self.assertIn("outcome_gain_pct_p10_p90", tier)
        self.assertEqual(len(tier["probability_meet_target_ci95"]), 2)
        self.assertEqual(len(tier["members"]), 2)
        self.assertTrue(isclose(
            sum(member["account_gain_contribution_pct"]["mean"] for member in tier["members"]),
            tier["expected_gain_pct"]["mean"],
            rel_tol=1e-10,
            abs_tol=1e-10,
        ))
        self.assertIn("ATK", tier["members"][0]["panel_delta"])
        self.assertIn("ATK", tier["members"][0]["panel"])
        self.assertEqual(
            tier["members"][0]["target_set_completion_probability"],
            0,
        )
        self.assertTrue(result["method"]["limitations"])

    def test_current_inventory_uses_shared_artifacts_and_preserves_four_piece_rules(self):
        result = estimate_account_farming_priorities(
            _two_loadouts_each(),
            _members(),
            domains=[1151],
            resin_budgets=[20],
            starting_inventory="inventory",
            sample_count=8,
            seed=29,
        )
        tier = result["domains"][0]["tiers"][0]
        self.assertEqual(tier["samples"], 8)
        self.assertEqual(
            tier["members"][0]["target_set_completion_probability"],
            1,
        )
        self.assertGreaterEqual(
            tier["members"][0]["expected_objective"]["mean"],
            tier["members"][0]["baseline"]["objective"],
        )

    def test_limits_domains_budgets_and_total_search_work(self):
        kwargs = {
            "members": _members(),
            "domains": [1151],
            "resin_budgets": [20],
            "sample_count": 8,
        }
        for updates in (
            {"domains": list(DOMAINS)[:9]},
            {"resin_budgets": [0]},
            {"resin_budgets": [20, 20]},
            {
                "domains": list(DOMAINS)[:2],
                "resin_budgets": [20, 40],
                "sample_count": 64,
            },
        ):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                estimate_account_farming_priorities(
                    [],
                    **{**kwargs, **updates},
                )


if __name__ == "__main__":
    unittest.main()
