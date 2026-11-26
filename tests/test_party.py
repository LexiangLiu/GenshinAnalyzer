import unittest

from artifact_sim import Simulator
from artifact_sim.damage_models import get_damage_model
from artifact_sim.loadout_optimizer import LoadoutPiece
from artifact_sim.party_optimizer import PartyMember, optimize_party
from artifact_sim.party_plan import auto_main_stats, plan_party


_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")
_MAINS = {
    15031: {"FLOWER": "HP", "PLUME": "ATK", "SANDS": "HP_PERCENT",
            "GOBLET": "HYDRO_DMG_BONUS", "CIRCLET": "CRIT_RATE"},
    15035: {"FLOWER": "HP", "PLUME": "ATK", "SANDS": "ATK_PERCENT",
            "GOBLET": "PYRO_DMG_BONUS", "CIRCLET": "CRIT_DMG"},
}
_SUBS = {
    15031: ("CRIT_RATE", "CRIT_DMG", "HP_PERCENT", "ENERGY_RECHARGE"),
    15035: ("CRIT_RATE", "CRIT_DMG", "ATK_PERCENT", "ENERGY_RECHARGE"),
}


def _pieces(sim, set_id, count=2):
    result = []
    for slot in _SLOTS:
        main = _MAINS[set_id][slot]
        pool = [stat for stat in _SUBS[set_id] if stat != main]
        for variant in range(count):
            subs = pool[variant:] + pool[:variant]
            artifact = sim.create(
                set_id, slot=slot, main_stat=main, initial_count=4,
                substats=[(stat, 4) for stat in subs],
            )
            result.append(LoadoutPiece(artifact))
    return result


def _members():
    return [
        PartyMember(
            model=get_damage_model("Neuvillette"),
            character={"id": "Neuvillette", "level": 90, "ascension": 6},
            weapon={"id": "Tome of the Eternal Flow", "level": 90, "ascension": 6},
            target_four_piece_set=15031,
            name="那维莱特",
        ),
        PartyMember(
            model=get_damage_model("阿蕾奇诺"),
            character={"id": "Arlecchino", "level": 90, "ascension": 6},
            weapon={"id": "Crimson Moon's Semblance", "level": 90, "ascension": 6},
            target_four_piece_set=15035,
            name="阿蕾奇诺",
        ),
    ]


class PartyOptimizerTests(unittest.TestCase):
    def test_disjoint_inventory_is_exact(self):
        sim = Simulator(3)
        pieces = _pieces(sim, 15031, 1) + _pieces(sim, 15035, 1)
        result = optimize_party(pieces, _members(), restarts=1, max_iterations=1)
        self.assertTrue(result.exact)
        self.assertEqual(result.conflicts, 0)
        self.assertEqual(len(result.members), 2)
        for member in result.members:
            self.assertEqual(len(member.loadout.artifacts()), 5)
        used = [piece for member in result.members for piece in member.loadout.artifacts()]
        self.assertEqual(len(used), len({id(piece) for piece in used}))

    def test_contested_inventory_stays_feasible(self):
        sim = Simulator(5)
        pieces = _pieces(sim, 15031, 2) + _pieces(sim, 15035, 1)
        members = [
            _members()[0],
            PartyMember(
                model=get_damage_model("莱欧斯利"),
                character={"id": "Wriothesley", "level": 90, "ascension": 6},
                weapon={"id": "Cashflow Supervision", "level": 90, "ascension": 6},
                target_four_piece_set=15031,
                name="莱欧斯利",
            ),
        ]
        result = optimize_party(pieces, members, restarts=2, max_iterations=2)
        self.assertFalse(result.exact)
        self.assertGreaterEqual(result.conflicts, 1)
        used = []
        for member in result.members:
            self.assertEqual(len(member.loadout.artifacts()), 5)
            used.extend(member.loadout.artifacts())
        self.assertEqual(len(used), len({id(piece) for piece in used}))
        self.assertLessEqual(result.total_objective, 2.0)

    def test_plan_ranks_domains_and_reports_per_member(self):
        sim = Simulator(9)
        pieces = _pieces(sim, 15031, 1) + _pieces(sim, 15035, 1)
        members = _members()
        candidate = [
            {slot: [main] for slot, main in _MAINS[15031].items()},
            {slot: [main] for slot, main in _MAINS[15035].items()},
        ]
        result = plan_party(
            pieces, members,
            domains=[982, 1151],
            threshold_pct=0.0,
            candidate_main_stats=candidate,
            restarts=1,
            max_iterations=1,
            max_nodes=200_000,
        )
        self.assertIn(result["recommended_domain_id"], (982, 1151))
        self.assertEqual(len(result["domains"]), 2)
        self.assertEqual({row["domain_id"] for row in result["domains"]}, {982, 1151})
        for domain in result["domains"]:
            self.assertEqual(len(domain["members"]), 2)
            self.assertGreaterEqual(domain["probability_any_member_upgrade"], 0.0)
            self.assertLessEqual(domain["probability_any_member_upgrade"], 1.0)
            for member in domain["members"]:
                self.assertGreaterEqual(member["probability_per_drop"], 0.0)
                self.assertLessEqual(member["probability_per_drop"], 1.0)
        self.assertEqual(auto_main_stats(members[0].model)["FLOWER"], ["HP"])


if __name__ == "__main__":
    unittest.main()
