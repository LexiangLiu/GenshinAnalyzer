import tempfile
import unittest
from pathlib import Path

from artifact_sim import Simulator
from artifact_sim.damage_models import get_damage_model
from artifact_sim.inventory import Inventory
from artifact_sim.loadout_optimizer import LoadoutPiece
from artifact_sim.plan import plan_single_character


_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")
_MAINS = {
    "FLOWER": "HP", "PLUME": "ATK", "SANDS": "HP_PERCENT",
    "GOBLET": "HYDRO_DMG_BONUS", "CIRCLET": "CRIT_RATE",
}


def _piece(sim, slot, variant):
    pool = ["CRIT_RATE", "CRIT_DMG", "HP_PERCENT", "HP", "ENERGY_RECHARGE", "ATK_PERCENT"]
    available = [stat for stat in pool if stat != _MAINS[slot]]
    chosen = available[variant:variant + 4]
    if len(chosen) < 4:
        chosen = available[:4]
    return sim.create(
        15031, slot=slot, main_stat=_MAINS[slot], initial_count=4,
        substats=[(stat, 4) for stat in chosen],
    )


class PlanTests(unittest.TestCase):
    def test_plan_returns_best_build_and_expected_resin(self):
        sim = Simulator(17)
        with tempfile.TemporaryDirectory() as directory:
            inventory = Inventory(Path(directory) / "artifacts.sqlite3")
            inventory.add([_piece(sim, slot, variant) for slot in _SLOTS for variant in (0, 1)])
            pieces = [
                LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
                for artifact_id, artifact in inventory.artifacts()
            ]
            self.assertEqual(len(pieces), 10)
            current = pieces[:1]
            result = plan_single_character(
                pieces,
                model=get_damage_model("那维莱特"),
                character={"id": "Neuvillette", "level": 90, "ascension": 6},
                weapon={"id": "Tome of the Eternal Flow", "level": 90, "ascension": 6},
                candidate_main_stats={
                    **{slot: [] for slot in _SLOTS},
                    "CIRCLET": ["CRIT_RATE"],
                },
                threshold_pct=1.0,
                current=current,
            )
        self.assertGreater(result["optimal_loadout"]["damage"], 0)
        self.assertTrue(any("未满级" in warning for warning in result["warnings"]))
        self.assertEqual(result["target_four_piece_set"]["id"], 15031)
        self.assertTrue(result["search"]["exact"])
        self.assertIsNotNone(result["current_loadout"])
        farming = result["farming"]
        self.assertEqual(farming["threshold_pct"], 1.0)
        domain, = farming["domains"]
        self.assertEqual(domain["domain_id"], 982)
        self.assertGreater(domain["probability_per_drop"], 0)
        self.assertGreater(domain["expected_resin"], 0)
        self.assertGreater(domain["resin_for_50pct"], 0)
        self.assertGreater(domain["resin_for_90pct"], 0)
        self.assertEqual(farming["recommended_domain_id"], 982)
        self.assertIn(domain["best_slot"], _SLOTS)
        self.assertAlmostEqual(
            result["optimal_loadout"]["damage"], farming["baseline_damage"], places=6,
        )


if __name__ == "__main__":
    unittest.main()
