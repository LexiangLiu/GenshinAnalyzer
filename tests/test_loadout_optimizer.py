import itertools
import unittest

from artifact_sim import Simulator
from artifact_sim.build_objective import character_weapon_panel
from artifact_sim.damage_models import DamageModel, get_damage_model
from artifact_sim.loadout_optimizer import LoadoutPiece, evaluate_loadout, optimize_loadout


_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")
_MAINS = {
    "FLOWER": "HP", "PLUME": "ATK", "SANDS": "HP_PERCENT",
    "GOBLET": "HYDRO_DMG_BONUS", "CIRCLET": "CRIT_RATE",
}


def _piece(sim, set_id, slot, variant):
    pool = ["CRIT_RATE", "CRIT_DMG", "HP_PERCENT", "HP", "ENERGY_RECHARGE", "ATK_PERCENT"]
    available = [stat for stat in pool if stat != _MAINS[slot]]
    chosen = available[variant:variant + 4]
    if len(chosen) < 4:
        chosen = available[:4]
    artifact = sim.create(
        set_id, slot=slot, main_stat=_MAINS[slot], initial_count=4,
        substats=[(stat, 4) for stat in chosen],
    )
    return LoadoutPiece(artifact, f"{slot}-{variant}")


class LoadoutOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.model = get_damage_model("那维莱特")
        self.base, _ = character_weapon_panel(
            {"id": "Neuvillette", "level": 90, "ascension": 6},
            {"id": "Tome of the Eternal Flow", "level": 90, "ascension": 6},
            self.model.panel_bonus_stats,
        )
        self.sim = Simulator(31)

    def _best_bruteforce(self, pieces, required=None):
        options = {slot: [piece for piece in pieces if piece.artifact.slot == slot] for slot in _SLOTS}
        best = None
        for choice in itertools.product(*(options[slot] for slot in _SLOTS)):
            counts = {}
            for piece in choice:
                counts[piece.artifact.set_id] = counts.get(piece.artifact.set_id, 0) + 1
            if required is not None and counts.get(required, 0) < 4:
                continue
            value = evaluate_loadout(choice, self.model, self.base)
            if best is None or value.damage > best.damage:
                best = value
        return best

    def test_exact_search_matches_bruteforce(self):
        pieces = [
            _piece(self.sim, 15031, slot, variant)
            for slot in _SLOTS for variant in (0, 1)
        ]
        result = optimize_loadout(pieces, self.model, self.base, required_four_piece_set=15031)
        expected = self._best_bruteforce(pieces, required=15031)
        self.assertTrue(result.exact)
        self.assertIsNotNone(expected)
        self.assertAlmostEqual(result.loadout.damage, expected.damage, places=9)
        self.assertGreaterEqual(result.loadout.set_counts[15031], 4)
        described = result.loadout.describe()
        self.assertEqual(len(described["pieces"]), 5)
        self.assertIn("final_panel", described)

    def test_required_four_piece_allows_only_one_offset(self):
        pieces = []
        for slot in _SLOTS:
            set_id = 15048 if slot == "GOBLET" else 15031
            pieces.extend(_piece(self.sim, set_id, slot, variant) for variant in (0, 1))
        result = optimize_loadout(pieces, self.model, self.base, required_four_piece_set=15031)
        expected = self._best_bruteforce(pieces, required=15031)
        self.assertAlmostEqual(result.loadout.damage, expected.damage, places=9)
        goblet = next(piece for piece in result.loadout.pieces if piece and piece.artifact.slot == "GOBLET")
        self.assertEqual(goblet.artifact.set_id, 15048)

    def test_generic_expression_model_is_not_character_specific(self):
        model = DamageModel(
            key="test_generic",
            character="Traveler",
            label="generic test model",
            expression="HP * (1 + min(100, CRIT_RATE) * CRIT_DMG / 10000)",
            monotone=True,
        )
        pieces = [_piece(self.sim, 15031, slot, 0) for slot in _SLOTS]
        result = optimize_loadout(pieces, model, self.base, required_four_piece_set=15031)
        self.assertGreater(result.loadout.damage, 0)
        self.assertAlmostEqual(
            result.loadout.damage,
            evaluate_loadout(pieces, model, self.base).damage,
            places=9,
        )

    def test_expected_full_level_piece_can_replace_current_best(self):
        pieces = [_piece(self.sim, 15031, slot, 0) for slot in _SLOTS]
        current_flower = pieces[0]
        projected_flower = LoadoutPiece(
            current_flower.artifact,
            "flower-projected",
            {"CRIT_RATE": 80.0, "CRIT_DMG": 160.0},
        )
        result = optimize_loadout(
            pieces + [projected_flower],
            self.model,
            self.base,
            required_four_piece_set=15031,
        )

        self.assertEqual(result.loadout.pieces[0], projected_flower)
        described_flower = result.loadout.describe()["pieces"][0]
        self.assertEqual(described_flower["state"]["level"], 0)
        self.assertEqual(described_flower["expected_full_level"]["level"], 20)
        self.assertAlmostEqual(described_flower["expected_full_level"]["substats"]["CRIT_RATE"], 80.0)

    def test_node_budget_returns_feasible_result_and_preserves_non_exact_flag(self):
        pieces = [
            _piece(self.sim, 15031, slot, variant)
            for slot in _SLOTS for variant in (0, 1)
        ]
        model = DamageModel(
            key="test_budget",
            character="Traveler",
            label="bounded search",
            expression="HP + ATK",
            monotone=False,
        )
        result = optimize_loadout(
            pieces,
            model,
            self.base,
            required_four_piece_set=15031,
            max_nodes=1,
        )

        self.assertFalse(result.exact)
        self.assertEqual(len(result.loadout.artifacts()), 5)

    def test_impossible_constraint_and_duplicate_slot(self):
        pieces = [_piece(self.sim, 15048, slot, 0) for slot in _SLOTS]
        with self.assertRaisesRegex(ValueError, "四件套"):
            optimize_loadout(pieces, self.model, self.base, required_four_piece_set=15031)
        duplicate = [_piece(self.sim, 15048, "FLOWER", 0), _piece(self.sim, 15048, "FLOWER", 1)]
        with self.assertRaisesRegex(ValueError, "同部位"):
            evaluate_loadout(duplicate, self.model, self.base)


if __name__ == "__main__":
    unittest.main()
