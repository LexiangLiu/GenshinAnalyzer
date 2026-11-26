import unittest

from artifact_sim import Simulator, get_damage_model
from artifact_sim.loadout_optimizer import LoadoutPiece
from artifact_sim.weapon_optimizer import (
    WeaponMember, WeaponOption, optimize_weapons_and_artifacts, weapon_variant,
)


_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")
_MAINS = {
    15035: {"FLOWER": "HP", "PLUME": "ATK", "SANDS": "ATK_PERCENT",
            "GOBLET": "PYRO_DMG_BONUS", "CIRCLET": "CRIT_DMG"},
    15020: {"FLOWER": "HP", "PLUME": "ATK", "SANDS": "ENERGY_RECHARGE",
            "GOBLET": "ELECTRO_DMG_BONUS", "CIRCLET": "CRIT_RATE"},
}
_POLE = "WEAPON_POLE"


def _pieces(sim, set_id, count=2):
    result = []
    for slot in _SLOTS:
        main = _MAINS[set_id][slot]
        subs = [stat for stat in ("CRIT_RATE", "CRIT_DMG", "ATK_PERCENT", "ENERGY_RECHARGE", "HP_PERCENT", "ATK") if stat != main][:4]
        for _ in range(count):
            artifact = sim.create(
                set_id, slot=slot, main_stat=main, initial_count=4,
                substats=[(stat, 4) for stat in subs],
            )
            result.append(LoadoutPiece(artifact))
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


class WeaponOptimizerTests(unittest.TestCase):
    def test_variant_merges_passive_and_weapon(self):
        base = get_damage_model("阿蕾奇诺")
        option = _weapons()[0]
        variant = weapon_variant(base, option)
        self.assertEqual(variant.assumptions["weapon"], option.name)
        self.assertGreaterEqual(variant.panel_bonus_stats.get("ATK_PERCENT", 0), 15.0)
        self.assertNotEqual(variant.expression, base.expression)

    def test_unique_weapon_and_artifact_assignment(self):
        sim = Simulator(31)
        pieces = _pieces(sim, 15035, 2) + _pieces(sim, 15020, 2)
        arlecchino = get_damage_model("阿蕾奇诺")
        raiden = get_damage_model("雷电将军")
        weapons = _weapons()
        members = [
            WeaponMember(
                character={"id": "Arlecchino", "level": 90, "ascension": 6},
                base_model=arlecchino,
                candidate_weapons=weapons,
                target_four_piece_set=arlecchino.target_four_piece_set,
                name="阿蕾奇诺",
            ),
            WeaponMember(
                character={"id": "Raiden Shogun", "level": 90, "ascension": 6},
                base_model=raiden,
                candidate_weapons=weapons,
                target_four_piece_set=raiden.target_four_piece_set,
                name="雷电将军",
            ),
        ]
        result = optimize_weapons_and_artifacts(
            pieces, members, max_nodes=20_000, restarts=0, max_iterations=1,
            normalize=False,
        )
        self.assertEqual(len(result.members), 2)
        assigned = [choice.weapon.name for choice in result.members]
        self.assertEqual(len(set(assigned)), 2)
        for choice in result.members:
            self.assertEqual(len(choice.loadout.artifacts()), 5)
            self.assertGreater(choice.damage, 0)
        self.assertTrue(all(
            f"SET_{choice.member.resolved_set()}_4" in choice.loadout.set_flags
            for choice in result.members
        ))


if __name__ == "__main__":
    unittest.main()
