import unittest
from dataclasses import replace

from artifact_sim import Simulator
from artifact_sim.__main__ import _party_members
from core.roll_probability import expected_full_level_substats
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
    def test_cli_and_optimizer_reject_multiple_traveler_forms_or_appearances(self):
        for members in (
            [{"character": "Aether"}, {"character": "Lumine"}],
            [
                {"character": "Traveler (Anemo)"},
                {"character": "travelergeo"},
            ],
        ):
            with self.subTest(members=members), self.assertRaisesRegex(
                ValueError, "一个队伍只能包含一个旅行者身份",
            ):
                _party_members({"members": members})

        model_members = [
            PartyMember(
                model=get_damage_model(form),
                character={"id": form},
            )
            for form in ("Traveler (Anemo)", "Traveler (Geo)")
        ]
        with self.assertRaisesRegex(ValueError, "一个队伍只能包含一个旅行者身份"):
            optimize_party([], model_members)

    def test_cli_member_request_applies_fixed_four_piece_set(self):
        members = _party_members({
            "members": [{
                "character": "Neuvillette",
                "weapon": {"id": "Tome of the Eternal Flow"},
                "fixed_four_piece_set": 15035,
            }],
        })

        member = members[0]["member"]
        self.assertEqual(member.fixed_four_piece_set, 15035)
        self.assertEqual(member.resolved_set(), 15035)

    def test_cli_catalog_model_uses_selected_set_and_account_talents(self):
        members = _party_members({
            "members": [{
                "character": "Arlecchino",
                "talents": {"auto": 8, "skill": 7, "burst": 6},
                "fixed_four_piece_set": 15001,
            }],
        })

        member = members[0]["member"]
        self.assertEqual(member.model.target_four_piece_set, 15001)
        self.assertIn("SET_15001_4", member.model.set_flags)
        self.assertEqual(member.model.assumptions["talents"]["auto"], 8)

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

    def test_members_can_use_distinct_fixed_sets(self):
        sim = Simulator(37)
        pieces = _pieces(sim, 15031, 1) + _pieces(sim, 15035, 1)
        members = [
            replace(_members()[0], fixed_four_piece_set=15035),
            replace(_members()[1], fixed_four_piece_set=15031),
        ]
        self.assertEqual(members[1].model.target_four_piece_set, 15031)

        result = optimize_party(pieces, members, restarts=1, max_iterations=1)

        self.assertTrue(result.exact)
        self.assertEqual(
            [member.target_four_piece_set for member in result.members],
            [15035, 15031],
        )
        for member in result.members:
            self.assertGreaterEqual(
                sum(piece.set_id == member.target_four_piece_set
                    for piece in member.loadout.artifacts()),
                4,
            )
        self.assertTrue(any("未被该模型覆盖" in warning for warning in result.warnings))

    def test_projected_multi_member_loadouts_share_inventory_without_reuse(self):
        sim = Simulator(11)
        actual = _pieces(sim, 15031, 1) + _pieces(sim, 15035, 1)
        projected = [
            LoadoutPiece(
                piece.artifact,
                piece.artifact_id,
                expected_full_level_substats(piece.artifact),
            )
            for piece in actual
        ]
        result = optimize_party(projected, _members(), restarts=1, max_iterations=1)
        selected = [
            piece
            for member in result.members
            for piece in member.loadout.pieces
            if piece is not None
        ]

        self.assertEqual(len(result.members), 2)
        self.assertEqual(len(selected), 10)
        self.assertEqual(len({id(piece.artifact) for piece in selected}), 10)
        self.assertTrue(all(
            piece.expected_full_level_substats is not None
            for member in result.members
            for piece in member.loadout.pieces
            if piece is not None
        ))

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
