import json
import random
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from artifact_sim import Artifact, Simulator, catalog, loadout
from tools.simulate_account import LEVEL_WEIGHTS, _sample_artifact_level


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulator(73)
        self.piece = self.sim.create(
            15048, slot="FLOWER", main_stat="HP", initial_count=3,
            substats=[("CRIT_RATE", 4), ("CRIT_DMG", 4), ("ATK_PERCENT", 4), ("ENERGY_RECHARGE", 4)],
        )

    def test_preview_is_visible_but_inactive_until_level_four(self):
        self.assertEqual(self.piece.describe()["pending_substat"], {
            "stat": "ENERGY_RECHARGE", "tier": 4, "value": 6.48, "activates_at": 4,
        })
        self.assertNotIn("ENERGY_RECHARGE", self.piece.substats())
        self.assertEqual(self.piece.score({"ENERGY_RECHARGE": 1}), 0)
        three = self.sim.enhance(self.piece, 3)
        self.assertEqual(three.preview, self.piece.preview)
        self.assertNotEqual(three.main_value, self.piece.main_value)

        restored = Artifact.from_state(json.loads(json.dumps(three.to_state())))
        four = Simulator(826).enhance(restored, 4)
        self.assertEqual(four, Simulator(0).enhance(self.piece, 4))
        self.assertEqual(four.substats(), {**self.piece.substats(), "ENERGY_RECHARGE": 6.48})
        self.assertEqual(four.score({"ENERGY_RECHARGE": 1}), 6.48)
        self.assertIsNone(four.preview)
        self.assertEqual(four.describe()["history"], [
            {"level": 4, "kind": "activate", "stat": "ENERGY_RECHARGE", "tier": 4, "value": 6.48},
        ])
        self.assertEqual(four.reset(), self.piece)

    def test_random_fourth_is_committed_before_enhancement(self):
        for seed in range(20):
            original = Simulator(seed).create(15047, initial_count=3)
            saved = Artifact.from_state(json.loads(json.dumps(original.to_state())))
            fourth = original.preview
            self.assertIsNotNone(fourth)
            self.assertEqual(len({roll.stat for roll in original.initial_rolls}), 4)
            self.assertNotIn(original.main_stat, {roll.stat for roll in original.initial_rolls})
            self.assertEqual(Simulator(seed + 999).enhance(saved, 4).substats()[fourth.stat], fourth.value)

    def test_upgrade_counts_and_incremental_equivalence(self):
        for initial_count in (3, 4):
            piece = self.sim.create(15048, slot="FLOWER", initial_count=initial_count)
            one_step = Simulator(71).enhance(piece, 20)
            engine = Simulator(71)
            incremental = piece
            for level in range(1, 21):
                incremental = engine.enhance(incremental, level)
            self.assertEqual(one_step, incremental)
            self.assertEqual(len(one_step.enhancements), initial_count + 1)
            self.assertEqual(one_step.main_value, 4780)
            self.assertEqual(len(one_step.substats()), 4)
            if initial_count == 4:
                self.assertIsNone(piece.preview)
                self.assertEqual(one_step.enhancements[0].level, 4)

    def test_targeted_roll_cannot_replace_activation(self):
        with self.assertRaisesRegex(ValueError, "只激活"):
            self.sim.enhance(self.piece, 4, overrides={4: ("CRIT_RATE", 4)})
        eight = self.sim.enhance(self.piece, 8, overrides={8: ("CRIT_RATE", 4)})
        self.assertEqual(eight.substats()["CRIT_RATE"], 7.78)
        self.assertEqual(eight.describe()["history"][1]["kind"], "increase")
        self.assertEqual(eight.describe()["history"][1]["value"], 3.89)

    def test_catalog_and_domain_weights(self):
        data = catalog()
        self.assertEqual(data["game_version"], "7.2")
        self.assertEqual(len(data["sets"]), 63)
        five_star = [item for item in data["sets"] if 5 in item["rarities"]]
        self.assertEqual(len(five_star), 46)
        self.assertEqual(len(data["domains"]), 22)
        for item in five_star:
            self.assertEqual(len(item["pieces"]), 5)
            self.assertEqual(set(item["effects"]), {"2", "4"})
            self.assertEqual(self.sim.create(item["name_en"]).set_id, item["id"])
        domain = next(item for item in data["domains"] if item["name"] == "逆悬的冰河")
        self.assertEqual(domain["set_ids"], [15047, 15048])
        pieces = [self.sim.drop(domain["name"]) for _ in range(5000)]
        set_counts = Counter(piece.set_id for piece in pieces)
        slot_counts = Counter(piece.slot for piece in pieces)
        self.assertEqual(set(set_counts), {15047, 15048})
        self.assertTrue(all(2300 <= count <= 2700 for count in set_counts.values()))
        self.assertEqual(len(slot_counts), 5)
        self.assertTrue(all(850 <= count <= 1150 for count in slot_counts.values()))
        self.assertTrue(850 <= sum(piece.initial_count == 4 for piece in pieces) <= 1150)
        self.assertEqual(self.sim.drop(domain["id"], four_line_probability=0).initial_count, 3)
        self.assertEqual(self.sim.drop(domain["id"], four_line_probability=1).initial_count, 4)

    def test_simulated_account_level_distribution_is_seeded_and_weighted(self):
        rng = random.Random(20261126)
        levels = [_sample_artifact_level(rng) for _ in range(20000)]
        counts = Counter(levels)

        self.assertAlmostEqual(sum(LEVEL_WEIGHTS.values()), 1.0)
        self.assertEqual(set(counts), set(LEVEL_WEIGHTS))
        self.assertGreater(counts[0], counts[20])
        self.assertGreater(counts[20], max(counts[level] for level in (4, 8, 12, 16)))
        for level, weight in LEVEL_WEIGHTS.items():
            with self.subTest(level=level):
                self.assertAlmostEqual(counts[level] / len(levels), weight, delta=0.015)

        def sample_sequence(seed):
            seeded_rng = random.Random(seed)
            return [_sample_artifact_level(seeded_rng) for _ in range(100)]

        self.assertEqual(sample_sequence(73), sample_sequence(73))

    def test_loadout_keeps_pending_out_until_active(self):
        plume = self.sim.create(15048, slot="PLUME", initial_count=3, substats=[
            ("DEF", 4), ("CRIT_DMG", 4), ("CRIT_RATE", 4), ("ENERGY_RECHARGE", 2),
        ])
        result = loadout([self.piece, plume])
        self.assertNotIn("ENERGY_RECHARGE", result["artifact_stats"])
        self.assertEqual(result["artifact_stats"]["CRIT_RATE"], 7.78)
        self.assertEqual(result["unlocked_set_bonuses"][0]["pieces"], 2)
        with self.assertRaisesRegex(ValueError, "同部位"):
            loadout([self.piece, self.piece])

    def test_invalid_data_fails_without_a_silent_random_fallback(self):
        with self.assertRaisesRegex(ValueError, "不支持主词条"):
            self.sim.create(15048, slot="FLOWER", main_stat="CRIT_DMG")
        with self.assertRaisesRegex(ValueError, "重复"):
            self.sim.create(15048, substats=[("DEF", 1), ("DEF", 4)])
        with self.assertRaisesRegex(ValueError, "相同"):
            self.sim.create(15048, slot="FLOWER", substats=[("HP", 1)])
        state = self.piece.to_state()
        state["level"] = 8
        with self.assertRaisesRegex(ValueError, "强化记录"):
            Artifact.from_state(state)

    def test_cli_round_trip_uses_fresh_processes_and_different_rng_seeds(self):
        def run(*args):
            return json.loads(subprocess.check_output(
                [sys.executable, "-m", "artifact_sim", *args], encoding="utf-8",
            ))

        before = run("create", "--set", "15048", "--slot", "flower", "--lines", "3", "--seed", "154")
        with tempfile.TemporaryDirectory() as directory:
            save = Path(directory) / "artifact.json"
            save.write_text(json.dumps(before), encoding="utf-8")
            after = run("enhance", str(save), "--to", "4", "--seed", "9")
            self.assertEqual(after["active_substats"][before["pending_substat"]["stat"]], before["pending_substat"]["value"])
            save.write_text(json.dumps(after), encoding="utf-8")
            self.assertEqual(run("reset", str(save)), before)


if __name__ == "__main__":
    unittest.main()
