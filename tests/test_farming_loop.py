import json
import tempfile
import unittest
from pathlib import Path

from artifact_sim import Inventory, LoadoutPiece, Simulator, get_damage_model
from artifact_sim.farming_loop import simulate_farming_loop
from artifact_sim.weapon_optimizer import WeaponMember, WeaponOption


_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")
_MAINS = {
    15035: {"FLOWER": "HP", "PLUME": "ATK", "SANDS": "ATK_PERCENT",
            "GOBLET": "PYRO_DMG_BONUS", "CIRCLET": "CRIT_DMG"},
    15020: {"FLOWER": "HP", "PLUME": "ATK", "SANDS": "ENERGY_RECHARGE",
            "GOBLET": "ELECTRO_DMG_BONUS", "CIRCLET": "CRIT_RATE"},
}
_POLE = "WEAPON_POLE"


def _artifacts(sim, set_id, count=2):
    result = []
    for slot in _SLOTS:
        main = _MAINS[set_id][slot]
        subs = [
            stat for stat in
            ("CRIT_RATE", "CRIT_DMG", "ATK_PERCENT", "ENERGY_RECHARGE", "HP_PERCENT", "ATK")
            if stat != main
        ][:4]
        for _ in range(count):
            artifact = sim.create(
                set_id, slot=slot, main_stat=main, initial_count=4,
                substats=[(stat, 4) for stat in subs],
            )
            result.append(artifact)
    return result


def _weapons():
    return [
        WeaponOption("Crimson Moon's Semblance", _POLE, refinement=1,
                     panel_bonus={"ATK_PERCENT": 15.0}, dmg_bonus=36.0),
        WeaponOption("Deathmatch", _POLE, refinement=5,
                     panel_bonus={"ATK_PERCENT": 32.0}),
        WeaponOption("Engulfing Lightning", _POLE, refinement=1,
                     panel_bonus={"ATK_PERCENT": 28.0}),
    ]


class FarmingLoopTests(unittest.TestCase):
    def test_loop_adds_drops_and_refreshes_builds(self):
        sim = Simulator(17)
        raw = _artifacts(sim, 15035, 1) + _artifacts(sim, 15020, 1)
        weapons = _weapons()

        with tempfile.TemporaryDirectory() as folder:
            inventory = Inventory(Path(folder) / "account.sqlite3")
            ids = inventory.add(raw)
            pieces = [
                LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
                for artifact_id, artifact in zip(ids, raw)
            ]
            def loadout_for(set_id):
                by_slot = {}
                for piece in pieces:
                    if piece.artifact.set_id == set_id:
                        by_slot.setdefault(piece.artifact.slot, piece)
                return [by_slot[slot] for slot in _SLOTS]

            arlecchino_pieces = loadout_for(15035)
            raiden_pieces = loadout_for(15020)
            arlecchino = get_damage_model("阿蕾奇诺")
            raiden = get_damage_model("雷电将军")
            members = [
                WeaponMember(
                    character={"id": "Arlecchino", "level": 90, "ascension": 6},
                    base_model=arlecchino,
                    candidate_weapons=weapons,
                    target_four_piece_set=15035,
                    name="阿蕾奇诺",
                    current_weapon=weapons[0],
                    current_pieces=arlecchino_pieces,
                ),
                WeaponMember(
                    character={"id": "Raiden Shogun", "level": 90, "ascension": 6},
                    base_model=raiden,
                    candidate_weapons=weapons,
                    target_four_piece_set=15020,
                    name="雷电将军",
                    current_weapon=weapons[2],
                    current_pieces=raiden_pieces,
                ),
            ]

            result = simulate_farming_loop(
                inventory,
                members,
                rounds=1,
                drops_per_round=1,
                domains=[1151],
                fixed_domain=1151,
                enhance_to=20,
                max_nodes=2_000,
                restarts=0,
                max_iterations=1,
                seed=5,
                threshold_pct=5.0,
            )

            self.assertEqual(result.total_drops, 1)
            self.assertEqual(result.total_resin, 20)
            self.assertEqual(len(inventory.artifacts()), len(raw) + 1)
            self.assertEqual(len(result.rounds), 1)
            self.assertEqual(result.rounds[0].domain_id, 1151)
            self.assertEqual(result.rounds[0].domain_name, "褪色的剧场")
            self.assertEqual(len(result.rounds[0].drops), 1)
            self.assertEqual(len(result.final_choices), 2)
            self.assertGreater(result.final_damage, 0)
            self.assertEqual(len({choice.weapon.name for choice in result.final_choices}), 2)
            self.assertIn(result.recommended_domain_id, (None, 1151))
            json.dumps(result.describe(), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
