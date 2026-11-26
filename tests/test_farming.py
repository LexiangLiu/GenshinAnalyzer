import unittest
from decimal import Decimal

from artifact_sim import Simulator, analyze_farming
from artifact_sim.build_objective import BuildObjective, character_weapon_panel
from artifact_sim.farming import _Budget, _summarize


class FarmingTests(unittest.TestCase):
    def test_target_set_automatically_selects_its_domain(self):
        sim = Simulator(15)
        gear = [sim.create(15031, slot=slot) for slot in ("FLOWER", "PLUME", "SANDS", "CIRCLET")]
        only_goblet = {slot: [] for slot in ("FLOWER", "PLUME", "SANDS", "CIRCLET")}
        only_goblet["GOBLET"] = ["HYDRO_DMG_BONUS"]
        result = analyze_farming(gear, objective="HYDRO_DMG_BONUS", drops=1,
                                 main_stats=only_goblet, target_four_piece_set="逐影猎人")
        domain, = result["domains_by_expected_gain"]
        self.assertEqual(domain["domain_id"], 982)
        self.assertEqual([row["id"] for row in domain["drop_sets"]], [15031, 15032])
        self.assertAlmostEqual(domain["overall"]["upgrade_probability"], 0.01)

    def test_known_weighted_circlet_and_four_piece_rules(self):
        sim = Simulator(10)
        gear = [sim.create(15048, slot=slot, main_stat=main, initial_count=4,
                           substats=[(stat, 1) for stat in ("DEF", "ATK_PERCENT", "DEF_PERCENT", "ENERGY_RECHARGE")])
                for slot, main in (("FLOWER", "HP"), ("PLUME", "ATK"), ("SANDS", "HP_PERCENT"),
                                   ("CIRCLET", "CRIT_RATE"))]
        only_circlet = {slot: [] for slot in ("FLOWER", "PLUME", "SANDS", "GOBLET")}
        only_circlet["CIRCLET"] = ["CRIT_RATE"]
        result = analyze_farming(gear, objective="max(20, CRIT_DMG - 50)", domains=[982, 2075], drops=100,
                                 main_stats=only_circlet, target_four_piece_set=15048)
        matching, other = result["domains_by_expected_gain"]
        self.assertEqual(matching["domain_id"], 2075)
        circlet = matching["slots"]["CIRCLET"]
        self.assertEqual(matching["slots_by_upgrade_probability"][0], "CIRCLET")
        self.assertAlmostEqual(circlet["single_drop_upgrade_probability"], 0.000551065506, places=12)
        self.assertAlmostEqual(circlet["upgrade_probability"], 0.05363006999, places=9)
        self.assertAlmostEqual(circlet["expected_objective"], 20.1748640091, places=8)
        self.assertEqual(other["overall"]["upgrade_probability"], 0)

        # Five matching pieces permit using an off-set circlet and retaining the four-set bonus.
        gear.append(sim.create(15048, slot="GOBLET"))
        other = analyze_farming(gear, objective="max(35, CRIT_DMG - 50)", domains=[982], drops=1,
                                main_stats=only_circlet, target_four_piece_set=15048)
        self.assertGreater(other["domains_by_expected_gain"][0]["overall"]["upgrade_probability"], 0)

    def test_claim_distribution_and_selected_substat_means(self):
        budget = _Budget(drops=None, resin=40, five_star_per_claim={"1": 0.75, "2": 0.25})
        # Mutually exclusive drop outcomes. Recorded increments are conditional on the score.
        law = {Decimal(2): [0.2, 0.2 * 6], Decimal(5): [0.1, 0.1 * 2]}
        result = _summarize(law, Decimal(1), [4.0], ("CRIT_DMG",), budget, None)
        cdf_1 = (0.75 * 0.7 + 0.25 * 0.7 ** 2) ** 2
        cdf_2 = (0.75 * 0.9 + 0.25 * 0.9 ** 2) ** 2
        self.assertAlmostEqual(result["upgrade_probability"], 1 - cdf_1)
        self.assertAlmostEqual(result["expected_gain"], (cdf_2 - cdf_1) + 4 * (1 - cdf_2))
        self.assertAlmostEqual(result["expected_final_substats"]["CRIT_DMG"],
                               4 + 6 * (cdf_2 - cdf_1) + 2 * (1 - cdf_2))

    def test_off_axis_substat_mean(self):
        gear = {slot: [] for slot in ("PLUME", "SANDS", "GOBLET", "CIRCLET")}
        result = analyze_farming([], objective="FLAT_HP", domains=[2075], drops=1,
                                 main_stats=gear, report_substats=["CRIT_DMG"])
        mean = result["domains_by_expected_gain"][0]["overall"]["expected_final_substats"]["CRIT_DMG"]
        # Every flower wins. Crit damage is present with the previously verified weighted inclusion chance.
        expected = 0.2 * (1646249348879 / 4813451763120) * (5.44 + 6.22 + 6.99 + 7.77) / 4 * (1 + 4.2 / 4)
        self.assertAlmostEqual(mean, expected, places=11)

    def test_min_upgrade_threshold_can_eliminate_upgrades(self):
        sim = Simulator(10)
        mains = {"FLOWER": "HP", "PLUME": "ATK", "SANDS": "HP_PERCENT", "CIRCLET": "CRIT_RATE"}
        gear = [
            sim.create(15048, slot=slot, main_stat=main, initial_count=4,
                       substats=[("DEF", 1), ("ATK_PERCENT", 1), ("DEF_PERCENT", 1), ("ENERGY_RECHARGE", 1)])
            for slot, main in mains.items()
        ]
        only_circlet = {slot: [] for slot in ("FLOWER", "PLUME", "SANDS", "GOBLET")}
        only_circlet["CIRCLET"] = ["CRIT_RATE"]
        common = dict(objective="max(20, CRIT_DMG - 50)", domains=[2075], drops=1,
                      main_stats=only_circlet, target_four_piece_set=15048)
        base = analyze_farming(gear, **common)
        strict = analyze_farming(gear, min_upgrade_pct=1000.0, **common)
        self.assertGreater(
            base["domains_by_expected_gain"][0]["overall"]["single_drop_upgrade_probability"], 0,
        )
        self.assertEqual(
            strict["domains_by_expected_gain"][0]["overall"]["single_drop_upgrade_probability"], 0,
        )
        self.assertEqual(strict["min_upgrade_pct"], 1000.0)

    def test_summary_only_matches_histogram_for_single_drop(self):
        sim = Simulator(12)
        mains = {"FLOWER": "HP", "PLUME": "ATK", "SANDS": "HP_PERCENT", "CIRCLET": "CRIT_RATE"}
        gear = [
            sim.create(15048, slot=slot, main_stat=main, initial_count=4,
                       substats=[("DEF", 1), ("ATK_PERCENT", 1), ("DEF_PERCENT", 1), ("ENERGY_RECHARGE", 1)])
            for slot, main in mains.items()
        ]
        main_stats = {slot: [] for slot in ("FLOWER", "PLUME", "SANDS", "GOBLET")}
        main_stats["CIRCLET"] = ["CRIT_RATE"]
        common = dict(objective="max(20, CRIT_DMG - 50)", domains=[2075], drops=1,
                      main_stats=main_stats, target_four_piece_set=15048, report_substats=())
        full = analyze_farming(gear, **common)["domains_by_expected_gain"][0]
        summary = analyze_farming(gear, summary_only=True, **common)["domains_by_expected_gain"][0]
        self.assertAlmostEqual(
            full["overall"]["single_drop_upgrade_probability"],
            summary["overall"]["single_drop_upgrade_probability"],
            places=12,
        )
        self.assertAlmostEqual(full["overall"]["expected_gain"], summary["overall"]["expected_gain"], places=9)
        for slot in full["slots"]:
            self.assertAlmostEqual(
                full["slots"][slot]["single_drop_upgrade_probability"],
                summary["slots"][slot]["single_drop_upgrade_probability"],
                places=12,
            )

    def test_neuvillette_base_panel_and_formula_validation(self):
        stats, _ = character_weapon_panel({"id": "Neuvillette", "level": 90},
                                          {"id": "Tome of the Eternal Flow", "level": 90}, {"HP_PERCENT": 16})
        formula = BuildObjective("HP * (1 + min(CRIT_RATE + 36, 100) * CRIT_DMG / 10000)")
        self.assertEqual(stats["CRIT_DMG"], Decimal("176.604800"))
        expected = 14695.093176 * 1.16 * (1 + 0.41 * (0.884 + 0.882048))
        self.assertAlmostEqual(float(formula.evaluate(stats, set())), expected, places=6)
        with self.assertRaises(ValueError):
            BuildObjective("__import__('sys')")


if __name__ == "__main__":
    unittest.main()
