import unittest
from dataclasses import replace

from artifact_sim import Simulator
from core.models import Roll
from core.roll_probability import expected_full_level_substats
from data.game_data import DATA, SUB_VALUES


class ExpectedFullLevelTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulator(31)
        self.four_line = self.sim.create(
            15031,
            slot="FLOWER",
            main_stat="HP",
            initial_count=4,
            substats=[
                ("CRIT_RATE", 1),
                ("CRIT_DMG", 2),
                ("ATK_PERCENT", 3),
                ("ENERGY_RECHARGE", 4),
            ],
        )

    def test_four_line_projection_uses_only_remaining_milestones(self):
        current = self.sim.enhance(self.four_line, 8)
        projected = expected_full_level_substats(current)
        remaining_events = 3
        for position, (stat, value) in enumerate(current.substats().items()):
            tier_mean = sum(value * weight for value, weight in zip(
                SUB_VALUES[stat], DATA["upgrade_tier_weights"],
            ))
            with self.subTest(stat=stat):
                self.assertAlmostEqual(
                    projected[stat] - value,
                    remaining_events * DATA["upgrade_slot_weights"][position] * tier_mean,
                )

    def test_three_line_unlock_ignores_hidden_preview_and_projects_fourth_line(self):
        three = self.sim.create(
            15031,
            slot="FLOWER",
            main_stat="HP",
            initial_count=3,
            substats=[("CRIT_RATE", 1), ("CRIT_DMG", 2), ("ATK_PERCENT", 3)],
        )
        altered_hidden_preview = replace(
            three,
            initial_rolls=three.initial_rolls[:3] + (Roll("ENERGY_RECHARGE", 4),),
        )
        projected = expected_full_level_substats(three)
        self.assertEqual(projected, expected_full_level_substats(altered_hidden_preview))
        current = three.substats()
        available = {
            stat: weight for stat, weight in DATA["substat_weights"].items()
            if stat != three.main_stat and stat not in current
        }
        total_weight = sum(available.values())
        for stat, selection_weight in available.items():
            probability = selection_weight / total_weight
            initial_mean = sum(
                value * weight for value, weight in zip(
                    SUB_VALUES[stat], DATA["tier_weights"],
                )
            )
            upgrade_mean = sum(
                value * weight for value, weight in zip(
                    SUB_VALUES[stat], DATA["upgrade_tier_weights"],
                )
            )
            expected = probability * (
                initial_mean + 4 * DATA["upgrade_slot_weights"][3] * upgrade_mean
            )
            with self.subTest(stat=stat):
                self.assertAlmostEqual(projected[stat], expected)
        for position, (stat, value) in enumerate(current.items()):
            expected = 4 * DATA["upgrade_slot_weights"][position] * sum(
                amount * weight for amount, weight in zip(
                    SUB_VALUES[stat], DATA["upgrade_tier_weights"],
                )
            )
            with self.subTest(stat=stat):
                self.assertAlmostEqual(projected[stat] - value, expected)

    def test_three_line_at_four_includes_known_unlock_without_readding_it(self):
        three = self.sim.create(
            15031,
            slot="FLOWER",
            main_stat="HP",
            initial_count=3,
            substats=[("CRIT_RATE", 1), ("CRIT_DMG", 2), ("ATK_PERCENT", 3)],
        )
        at_four = self.sim.enhance(three, 4)
        projected = expected_full_level_substats(at_four)
        self.assertEqual(set(projected), set(at_four.substats()))
        self.assertGreater(projected[at_four.initial_rolls[3].stat], at_four.substats()[at_four.initial_rolls[3].stat])

    def test_level_twenty_projection_is_exactly_current_stats(self):
        full = self.sim.enhance(self.four_line, 20)
        self.assertEqual(expected_full_level_substats(full), full.substats())


if __name__ == "__main__":
    unittest.main()
