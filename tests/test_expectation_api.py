import json
import threading
import unittest
from urllib.request import Request, urlopen

from tools.acceptance_ui import AcceptanceHTTPServer, AcceptanceService


class ResinExpectationApiTests(unittest.TestCase):
    def setUp(self):
        self.service = AcceptanceService()
        self.addCleanup(self.service._temporary_directory.cleanup)
        self.service._session = {
            "id": "test-session",
            "account_dir": self.service._root / "unused-account-dir",
            "optimization": {
                "id": "test-optimization",
                "request": {
                    "seed": 73,
                    "domains": [1151, 1152],
                    "members": [
                        {
                            "character": "Arlecchino",
                            "name": "Arlecchino",
                            "level": 90,
                            "ascension": 6,
                            "weapon": None,
                            "formula_objective": {
                                "mode": "single",
                                "formulas": [
                                    {"key": "arlecchino_normal_3", "weight": 1},
                                ],
                            },
                        },
                    ],
                },
            },
        }

    def test_expectations_reuse_the_optimized_formula_and_return_aggregates(self):
        result = self.service.expectations({
            "session_id": "test-session",
            "optimization_id": "test-optimization",
            "domain_id": 1151,
            "starting_inventory": "zero",
            "resin_budgets": [0],
            "sample_count": 8,
        })
        self.assertEqual(result["starting_inventory"], "zero")
        self.assertEqual(result["method"]["seed"], 73)
        self.assertEqual(result["domain"]["id"], 1151)
        self.assertEqual(result["tiers"][0]["resin"], 0)
        self.assertEqual(
            list(result["formula_labels"]),
            ["arlecchino_normal_3"],
        )

    def test_expectations_keep_the_optimized_four_piece_constraint(self):
        self.service._session["optimization"]["request"]["members"][0][
            "fixed_four_piece_set"
        ] = 15035
        result = self.service.expectations({
            "session_id": "test-session",
            "optimization_id": "test-optimization",
            "domain_id": 1151,
            "starting_inventory": "zero",
            "resin_budgets": [0],
            "sample_count": 8,
        })
        self.assertEqual(result["required_four_piece_set"]["id"], 15035)
        self.assertEqual(result["baseline"]["search"]["required_four_piece_set"], None)
        self.assertEqual(result["tiers"][0]["feasible_samples"], 0)
        self.assertIsNone(result["tiers"][0]["objective"]["mean"])

    def test_expectations_http_route_returns_the_aggregate_result(self):
        server = AcceptanceHTTPServer(("127.0.0.1", 0), self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/expectations",
            data=json.dumps({
                "session_id": "test-session",
                "optimization_id": "test-optimization",
                "domain_id": 1151,
                "starting_inventory": "zero",
                "resin_budgets": [0],
                "sample_count": 8,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertEqual(result["tiers"][0]["resin"], 0)
        self.assertEqual(result["method"]["kind"], "monte_carlo_expected_optimum")

    def test_expectations_require_the_current_single_character_optimization(self):
        with self.assertRaisesRegex(ValueError, "单角色配装优化"):
            self.service.expectations({
                "session_id": "test-session",
                "optimization_id": "stale",
                "domain_id": 1151,
                "resin_budgets": [0],
                "starting_inventory": "zero",
                "sample_count": 8,
            })

        self.service._session["optimization"]["request"]["members"].append(
            {"character": "Xianyun"},
        )
        with self.assertRaisesRegex(ValueError, "只支持一个角色"):
            self.service.expectations({
                "session_id": "test-session",
                "optimization_id": "test-optimization",
                "domain_id": 1151,
                "resin_budgets": [0],
                "starting_inventory": "zero",
                "sample_count": 8,
            })

    def test_account_farming_uses_selected_multi_role_formula_objectives(self):
        self.service._session["optimization"]["request"]["members"][0][
            "formula_objective"
        ] = {
            "mode": "relative",
            "formulas": [
                {"key": "arlecchino_skill_finaldmg", "weight": 0.6},
                {"key": "arlecchino_burst", "weight": 0.4},
            ],
        }
        self.service._session["optimization"]["request"]["members"].append({
            "character": "Xianyun",
            "name": "Xianyun",
            "level": 90,
            "ascension": 6,
        })
        result = self.service.account_farming({
            "session_id": "test-session",
            "optimization_id": "test-optimization",
            "domains": [1151],
            "starting_inventory": "zero",
            "resin_budgets": [20],
            "sample_count": 8,
            "target_gain_pct": 1,
        })
        tier = result["domains"][0]["tiers"][0]
        self.assertEqual(result["starting_inventory"], "zero")
        self.assertEqual(result["method"]["kind"], "monte_carlo_shared_inventory_expected_optimum")
        self.assertEqual(tier["samples"], 8)
        self.assertEqual(len(tier["members"]), 2)
        objective = tier["members"][0]["formula_objective"]
        self.assertEqual(objective["mode"], "relative")
        self.assertEqual(
            [formula["key"] for formula in objective["weights"]],
            ["arlecchino_skill_finaldmg", "arlecchino_burst"],
        )
        self.assertEqual(
            tier["members"][0]["formulas"]["arlecchino_skill_finaldmg"]["weight"],
            0.6,
        )
        self.assertEqual(
            tier["members"][0]["formulas"]["arlecchino_burst"]["weight"],
            0.4,
        )

    def test_account_farming_validates_optimization_and_domain_scope(self):
        request = {
            "session_id": "test-session",
            "optimization_id": "stale",
            "domains": [1151],
            "starting_inventory": "zero",
            "resin_budgets": [20],
            "sample_count": 8,
        }
        with self.assertRaisesRegex(ValueError, "配装优化"):
            self.service.account_farming(request)
        request["optimization_id"] = "test-optimization"
        request["domains"] = [999999]
        with self.assertRaisesRegex(ValueError, "可用列表"):
            self.service.account_farming(request)

    def test_account_farming_http_route_returns_domain_rankings(self):
        server = AcceptanceHTTPServer(("127.0.0.1", 0), self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/account-farming",
            data=json.dumps({
                "session_id": "test-session",
                "optimization_id": "test-optimization",
                "domains": [1151],
                "starting_inventory": "zero",
                "resin_budgets": [20],
                "sample_count": 8,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertEqual(result["rankings"][0]["resin"], 20)
        self.assertEqual(result["rankings"][0]["domains"][0]["rank"], 1)


if __name__ == "__main__":
    unittest.main()
