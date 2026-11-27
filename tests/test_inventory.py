import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from artifact_sim import Inventory, Simulator, characters, loadout, mastery_bonus, score_weights
from artifact_sim.legacy import legacy_backup


class InventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / "artifacts.sqlite3"
        self.inv = Inventory(self.db)
        self.sim = Simulator(84)

    def piece(self, slot="FLOWER", set_id=15048):
        return self.sim.create(set_id, slot=slot, initial_count=3)

    def test_collection_lifecycle_survives_reopen(self):
        flower, plume, goblet = self.inv.add([self.piece(), self.piece("PLUME"), self.piece("GOBLET", 15047)])
        self.assertEqual({artifact_id for artifact_id, _ in self.inv.artifacts()}, {flower, plume, goblet})
        self.assertTrue(self.inv.get(flower)["is_new"])
        self.assertFalse(self.inv.flags(flower, seen=True)["is_new"])
        preview = self.inv.get(flower)["pending_substat"]
        at4 = self.inv.enhance(flower, self.sim)
        self.assertEqual(at4["active_substats"][preview["stat"]], preview["value"])
        self.inv.enhance(goblet, self.sim, 20)
        self.inv.flags(goblet, locked=True)
        self.assertEqual([row["id"] for row in self.inv.list(sort="lvdesc")], [goblet, flower, plume])
        self.assertEqual([row["id"] for row in self.inv.list(sort="lvasc")], [plume, flower, goblet])
        self.assertEqual([row["id"] for row in self.inv.list(slots=["PLUME", "GOBLET"], sets=["炉火融炼之心"], main_stats=["ATK"])], [plume])
        self.assertEqual(self.inv.counts()["total"], 3)
        self.assertEqual(self.inv.reset(), [flower])
        self.assertEqual(self.inv.get(flower)["pending_substat"], preview)
        with self.assertRaisesRegex(ValueError, "上锁"):
            self.inv.reset(goblet)
        with self.assertRaisesRegex(ValueError, "上锁"):
            self.inv.delete(goblet)
        self.inv.equip("Albedo", [plume])
        self.assertEqual([artifact_id for artifact_id, _ in self.inv.equipped_artifacts("Albedo")], [plume])
        with self.assertRaisesRegex(ValueError, "已装备"):
            self.inv.delete(plume)
        self.inv.delete(flower)
        self.assertEqual(Inventory(self.db).undo_delete(), flower)
        self.assertEqual(self.inv.clear(), 1)
        self.assertIsNone(self.inv.undo_delete())
        self.assertEqual(self.inv.counts()["total"], 2)
        self.inv.wipe()
        self.assertEqual(self.inv.counts()["total"], 0)
        self.assertEqual(self.inv.profiles(), [])

    def test_equip_preview_and_swap_maintain_unique_slots(self):
        first, second, plume = self.inv.add([self.piece(), self.piece(set_id=15047), self.piece("PLUME", 15047)])
        self.inv.equip("阿贝多", [first, plume])
        self.inv.equip("Lumine", [second])
        preview = self.inv.equip("10000038", [second], preview=True)
        self.assertEqual(preview["before"]["static_set_stats"], {})
        self.assertEqual(preview["after"]["static_set_stats"], {"ATK_PERCENT": 18.0})
        self.assertEqual(self.inv.get(second)["equipped"], "Traveler")
        self.assertEqual(self.inv.equip("Albedo", [second]), preview)
        self.assertEqual(self.inv.get(first)["equipped"], "Traveler")
        self.assertEqual(self.inv.get(second)["equipped"], "Albedo")
        self.assertEqual(self.inv.panel("阿贝多")["equipped"]["FLOWER"]["id"], second)
        self.assertEqual(self.inv.unequip("Albedo", ["FLOWER"]), [second])
        self.inv.profile("Lumine", rename="测试配装")
        self.assertEqual(self.inv.get(first)["equipped"], "测试配装")
        self.inv.profile("测试配装", delete=True)
        self.assertIsNone(self.inv.get(first)["equipped"])

    def test_traveler_element_forms_share_one_inventory_profile(self):
        artifact_id, = self.inv.add([self.piece()])
        self.assertEqual(self.inv.equip("Traveler (Anemo)", [artifact_id])["profile"], "Traveler")
        self.assertEqual(self.inv.profile("Traveler (Geo)"), "Traveler")
        self.assertEqual(self.inv.equip("Aether", [artifact_id])["profile"], "Traveler")
        self.assertEqual(self.inv.profiles(), ["Traveler"])
        self.assertEqual(self.inv.get(artifact_id)["equipped"], "Traveler")

    def test_panel_applies_static_bonuses_and_leaves_conditionals_as_text(self):
        pieces = [self.piece(slot, 15025) for slot in ("FLOWER", "PLUME", "SANDS", "GOBLET")]
        panel = loadout(pieces)
        self.assertEqual(panel["static_set_stats"], {"DENDRO_DMG_BONUS": 15})
        self.assertEqual([entry["pieces"] for entry in panel["unlocked_set_bonuses"]], [2, 4])
        self.assertEqual(panel["mastery_bonus"], mastery_bonus(panel["panel_stats"].get("ELEMENTAL_MASTERY", 0)))
        pieces = [self.piece(slot, 14002) for slot in ("FLOWER", "PLUME")]
        self.assertEqual(loadout(pieces)["static_set_stats"], {"ELECTRO_RES": 40})

    def test_custom_scores_languages_and_character_filters(self):
        piece = self.sim.create(15048, slot="FLOWER", initial_count=3, substats=[
            ("ATK", 4), ("CRIT_RATE", 4), ("CRIT_DMG", 4), ("ENERGY_RECHARGE", 4),
        ])
        artifact_id, = self.inv.add([piece])
        self.assertAlmostEqual(piece.score(), 19.45 * 0.13 + 3.89 * 2 + 7.77)
        self.inv.preferences({"language": "en", "score_rules": ["crit", "er"], "high_score": 15})
        item = self.inv.get(artifact_id)
        self.assertEqual(item["score"], 15.55)
        self.assertTrue(item["high_score"])
        self.assertEqual(item["set_name"], "Heart of the Furnace")
        self.assertEqual(self.inv.preferences(reset=True)["score_rules"], ["default"])
        self.assertEqual(score_weights(["hp", "em"])["ELEMENTAL_MASTERY"], 0.339)
        filtered = characters(element="ELEMENT_GEO", weapon="WEAPON_SWORD_ONE_HAND", region="Mondstadt")
        self.assertEqual([row["name"] for row in filtered], ["阿贝多"])

    def test_backup_round_trip_and_import_rolls_back_on_collision(self):
        flower, plume = self.inv.add([self.piece(), self.piece("PLUME")])
        self.inv.equip("Albedo", [flower])
        self.inv.delete(plume)
        self.inv.preferences({"language": "en", "score_rules": ["crit"]})
        snapshot = self.inv.export()
        clone = Inventory(self.root / "clone.sqlite3")
        self.assertEqual(clone.restore(json.loads(json.dumps(snapshot)), replace=True), 2)
        self.assertEqual(clone.export(), snapshot)
        with self.assertRaises(sqlite3.IntegrityError):
            clone.restore(snapshot)
        self.assertEqual(clone.export(), snapshot)
        invalid = json.loads(json.dumps(snapshot))
        invalid["artifacts"][1]["state"]["level"] = 20
        with self.assertRaises(ValueError):
            clone.restore(invalid, replace=True)
        self.assertEqual(clone.export(), snapshot)
        self.assertEqual(clone.undo_delete(), plume)

    def test_concurrent_writes_keep_artifacts_and_each_enhancement(self):
        artifact_id, = self.inv.add([self.piece()])
        def enhance(seed):
            return Inventory(self.db).enhance(artifact_id, Simulator(seed))["state"]["level"]
        with ThreadPoolExecutor(max_workers=5) as pool:
            levels = list(pool.map(enhance, range(5)))
        self.assertEqual(sorted(levels), [4, 8, 12, 16, 20])
        self.assertEqual(len(self.inv.get(artifact_id)["history"]), 5)

    def test_legacy_migration_keeps_existing_roll_history_and_preview(self):
        entry = [["CRITRate", 3.8889], ["ATKPer", 5.8333], ["CRITDMG", 7.7718]]
        old = {
            "symbol": "old-1", "set": "Bizzard Strayer", "part": "Flower", "mainEntry": "HP",
            "level": 8, "initEntry": json.dumps(entry), "lock": True, "isNew": False, "equipped": "Lumine",
            "upgradeHistory": [["energyRecharge", 6.475], ["ATKPer", 5.8333]],
            "entry": [["CRITRate", 3.8889], ["ATKPer", 11.6666], ["CRITDMG", 7.7718], ["energyRecharge", 6.475]],
        }
        pending = {**old, "symbol": "old-2", "level": 0, "entry": entry, "upgradeHistory": [], "equipped": 0}
        backup = legacy_backup([old, pending])
        self.assertEqual(backup, legacy_backup([old, pending]))
        self.inv.restore(backup)
        item = self.inv.get("old-1")
        self.assertTrue(item["locked"])
        self.assertEqual(item["equipped"], "Traveler")
        self.assertEqual(item["history"][0]["stat"], "ENERGY_RECHARGE")
        self.assertEqual(item["history"][1]["stat"], "ATK_PERCENT")
        self.assertIsNotNone(self.inv.get("old-2")["pending_substat"])

    def test_cli_inventory_persistence_across_processes(self):
        def run(*args):
            return json.loads(subprocess.check_output(
                [sys.executable, "-m", "artifact_sim", "inventory", "--db", str(self.db), *args],
                encoding="utf-8",
            ))
        ids = run("add", "--set", "炉火融炼之心", "--slot", "flower", "--lines", "3", "--count", "2", "--seed", "7")["ids"]
        preview = run("get", ids[0])["pending_substat"]
        four = run("enhance", ids[0])
        self.assertEqual(four["active_substats"][preview["stat"]], preview["value"])
        self.assertEqual(run("enhance", ids[0], "--to", "8", "--force", "8::4")["history"][1]["tier"], 4)
        self.assertEqual(run("counts")["total"], 2)
