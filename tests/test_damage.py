import hashlib
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from artifact_sim import calculate_damage, damage_engine_info, list_damage_formulas
from artifact_sim import damage


_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "neuvillette-damage.json"


class DamageBundleTests(unittest.TestCase):
    def test_bundle_matches_pinned_manifest(self):
        manifest = damage_engine_info()
        self.assertEqual(manifest["commit"], "916e46d14d1031c9188a6122f045f3308b0a1f37")
        self.assertEqual(manifest["license"], "MIT")
        self.assertIn("libs/gi/formula", manifest["internal_libraries"])
        self.assertIn("libs/gi/stats", manifest["internal_libraries"])
        self.assertIn("libs/pando/engine", manifest["internal_libraries"])
        path = Path(damage.__file__).with_name("go_damage")
        self.assertEqual(hashlib.sha256((path / "bridge.mjs").read_bytes()).hexdigest(), manifest["sha256"])
        self.assertTrue((path / "LICENSE-genshin-optimizer.txt").is_file())
        self.assertTrue((path / "LICENSE-fflate.txt").is_file())


class DamageRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("Node.js is required for damage computation")
        version = subprocess.check_output([node, "--version"], encoding="utf-8").strip()
        if int(version.lstrip("v").split(".")[0]) < 24:
            raise unittest.SkipTest("Node.js 24 or newer is required for damage computation")

    def setUp(self):
        self.request = json.loads(_EXAMPLE.read_text(encoding="utf-8"))

    def test_neuvillette_damage_from_cli(self):
        output = subprocess.check_output(
            [sys.executable, "-m", "artifact_sim", "damage", "evaluate", str(_EXAMPLE)],
            encoding="utf-8",
        )
        result = json.loads(output)
        self.assertAlmostEqual(result["panel"]["hp"], 39695.093176, places=6)
        self.assertAlmostEqual(result["panel"]["critRate_"], 0.6)
        self.assertEqual([attack["element"] for attack in result["attacks"]], ["hydro"] * 3)
        for row, expected in zip(result["attacks"], (15205.568356956, 13370.097746777, 23133.504920565)):
            self.assertAlmostEqual(row["damage"], expected, places=6)

    def test_constellation_talent_and_crit_mode_change_results(self):
        c0 = calculate_damage({**self.request, "constellation": 0})
        c2 = calculate_damage(self.request)
        c3 = calculate_damage({**self.request, "constellation": 3})
        noncrit = calculate_damage({**self.request, "mode": "noncrit"})
        lower_talent = calculate_damage({**self.request, "talents": {"auto": 9, "skill": 10, "burst": 10}})
        self.assertAlmostEqual(c0["attacks"][0]["damage"], 13365.478448219, places=6)
        self.assertGreater(c2["attacks"][0]["damage"], c0["attacks"][0]["damage"])
        self.assertEqual(c2["attacks"][1:], c0["attacks"][1:])
        self.assertEqual(c3["effective_talents"]["auto"], 13)
        self.assertAlmostEqual(c3["attacks"][0]["damage"], 18425.571067840, places=6)
        self.assertAlmostEqual(noncrit["attacks"][0]["damage"], 7301.944082288, places=6)
        self.assertLess(lower_talent["attacks"][0]["damage"], c2["attacks"][0]["damage"])

    def test_weapons_resistance_and_formula_discovery(self):
        catalog = list_damage_formulas("neuvillette")
        self.assertIn("charged_judgment", {entry["name"] for entry in catalog["attacks"]})
        self.assertEqual(catalog["conditions"]["a1Stacks"]["max"], 3)
        weapon = calculate_damage({
            **self.request, "weapon": {"key": "PrototypeAmber", "level": 90, "ascension": 6},
        })
        self.assertAlmostEqual(weapon["panel"]["hp"], 45770.926400549, places=6)
        resistance = calculate_damage({
            **self.request, "enemy": {"resistance": 0.1, "resistances": {"hydro": 0.5}},
        })
        self.assertAlmostEqual(resistance["attacks"][0]["damage"], 8447.537976086, places=6)

    def test_rejects_unsupported_formula_and_invalid_inputs(self):
        for patch, error in (
            ({"attacks": ["nonexistent"]}, "Unknown damage formula"),
            ({"conditions": {"a1Stacks": 4}}, "Invalid value for condition"),
            ({"bonus_stats": {"crit_rate": 0.5}}, "Unknown bonus stat"),
            ({"constellation": 7}, "constellation must be"),
        ):
            with self.subTest(patch=patch), self.assertRaisesRegex(ValueError, error):
                calculate_damage({**self.request, **patch})

    def test_list_condition_is_translated_to_the_upstream_index(self):
        base = {
            "character": "Albedo", "level": 90, "ascension": 6, "constellation": 0,
            "talents": {"auto": 10, "skill": 10, "burst": 10}, "attacks": ["burst"],
        }
        self.assertEqual(calculate_damage(base)["panel"]["eleMas"], 0)
        active = calculate_damage({**base, "conditions": {"burstUsed": "burstUsed"}})
        self.assertEqual(active["panel"]["eleMas"], 125)
