import gzip
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

import artifact_sim.references as references
from artifact_sim import catalog, game_data_info, get_character_profile, get_game_record, get_game_stats, list_game_records


class ReferenceDataTests(unittest.TestCase):
    def test_all_snapshot_files_match_manifest(self):
        manifest = game_data_info()
        root = Path(references.__file__).with_name("reference_data")
        self.assertEqual(manifest["game_version"], catalog()["game_version"])
        self.assertEqual(len(manifest["categories"]), 35)
        self.assertEqual(sum(row["counts"]["zh"] for row in manifest["categories"].values()), 9382)
        for category, desc in manifest["categories"].items():
            with self.subTest(category=category):
                for language in manifest["languages"]:
                    payload = gzip.decompress((root / f"{category}.{language}.json.gz").read_bytes())
                    self.assertEqual(hashlib.sha256(payload).hexdigest(), desc["sha256"][language])
                    self.assertEqual(len(json.loads(payload)), desc["counts"][language])
        for category, desc in manifest["stats"].items():
            payload = gzip.decompress((root / f"{category}.stats.json.gz").read_bytes())
            self.assertEqual(hashlib.sha256(payload).hexdigest(), desc["sha256"])
            self.assertEqual(len(json.loads(payload)), desc["count"])

    def test_72_characters_and_weapon_are_linked_to_skills_and_materials(self):
        chars = list_game_records("characters", version="7.2")
        self.assertEqual({row["id"] for row in chars}, {10000136, 10000137})
        self.assertEqual([row["id"] for row in list_game_records("weapons", version="7.2")], [14525])
        profiles = [get_character_profile("米提亚"), get_character_profile("Valeriy")]
        for profile in profiles:
            self.assertEqual(len(profile["talents"]), 1)
            self.assertEqual(len(profile["constellations"]), 1)
            self.assertTrue(profile["talents"][0]["combat2"]["attributes"]["parameters"])
            self.assertIn("c6", profile["constellations"][0])
        weapon = get_game_record("weapons", "秘星典谕", language="en")
        self.assertEqual(weapon["name"], "Starsealed Edict")
        self.assertTrue(all(weapon[f"r{rank}"] for rank in range(1, 6)))
        for row in [weapon, *(record for p in profiles for record in (p["character"], *p["talents"]))]:
            for cost_group in row.get("costs", {}).values():
                for item in cost_group:
                    self.assertEqual(get_game_record("materials", item["id"])["id"], item["id"])

    def test_stats_keep_both_sides_of_ascension(self):
        before = get_game_stats("characters", "Mitya", 80)
        after = get_game_stats("characters", 10000136, 80, ascension="after")
        self.assertEqual((before["ascension"], after["ascension"]), (5, 6))
        self.assertLess(before["hp"], after["hp"])
        self.assertEqual(after, get_game_stats("characters", "米提亚", 80, ascension=6))
        stats = get_game_stats("weapons", 14525, 90)
        self.assertAlmostEqual(stats["attack"], 541.829758)
        self.assertAlmostEqual(stats["specialized"], 0.882048)
        with self.assertRaisesRegex(ValueError, "等级范围"):
            get_game_stats("weapons", 14525, 91)

    def test_latest_missing_records_are_supplemented(self):
        for name in ("新基捷城", "如翼之峰"):
            self.assertEqual(get_game_record("areas", name)["version"], "7.2")
        enemy = get_game_record("enemies", "Wavebinder")
        self.assertEqual(enemy["id"], 51346)
        self.assertEqual(len(enemy["variants"]), 5)
        self.assertEqual(get_game_stats("enemies", "浪涛锚定者", 90)["monster_id"], enemy["monsterId"])
        self.assertEqual(get_game_record("materials", "破碎的稳定锚")["rarity"], 4)
        self.assertEqual(get_game_record("tcgcharactercards", "法尔伽")["version"], "7.2")
        for name in ("浮槃之歌", "重光临晨", "升华赞歌", "昔时之歌", "彩特琳德", "元素幻变：燃烧祝佑"):
            self.assertEqual(get_game_record("tcgactioncards", name)["version"], "7.2")

    def test_filters_locale_fallback_and_read_only_cache(self):
        result = list_game_records("weapons", search="starsealed", filters={"weaponType": "WEAPON_CATALYST", "rarity": 5})
        self.assertEqual([row["id"] for row in result], [14525])
        records = list_game_records("weapons", language="en", limit=1, offset=1)
        self.assertEqual(records, list_game_records("weapons", language="en", limit=2)[1:])
        game_record = get_game_record("weapons", 14525)
        game_record["costs"].clear()
        self.assertTrue(get_game_record("weapons", 14525)["costs"])
        self.assertEqual(game_data_info()["categories"]["elements"]["source_languages"]["zh"], "en")
        self.assertEqual(get_game_record("elements", "pyro")["name"], "Pyro")
        self.assertEqual(len(get_character_profile("空")["talents"]), 7)
        self.assertEqual(get_character_profile("奇偶·男性")["talents"], [])

    def test_offline_cli(self):
        result = subprocess.check_output(
            [sys.executable, "-m", "artifact_sim", "data", "list", "weapons",
             "--version", "7.2", "--where", "rarity=5", "--language", "en"],
            encoding="utf-8",
        )
        self.assertEqual(json.loads(result)[0]["name"], "Starsealed Edict")
