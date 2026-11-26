import math
import unittest

from artifact_sim.resin import estimate_first_upgrade


class ResinTests(unittest.TestCase):
    def test_geometric_waiting_times(self):
        result = estimate_first_upgrade(0.1, 100, 10_000, resin_per_claim=20)
        self.assertAlmostEqual(result["expected_claims"], 10)
        self.assertAlmostEqual(result["expected_resin"], 200)
        self.assertAlmostEqual(result["resin_for_50pct"], 140)
        self.assertAlmostEqual(result["resin_for_90pct"], 440)
        self.assertAlmostEqual(result["expected_gain_per_upgrade"], 1000)
        self.assertAlmostEqual(result["expected_gain_pct_per_drop"], 1)
        self.assertAlmostEqual(result["expected_gain_pct_per_100_resin"], 5)
        self.assertAlmostEqual(
            result["probability_within_horizon"],
            1 - math.pow(0.9, 50),
            places=12,
        )

    def test_zero_and_certain_probability(self):
        empty = estimate_first_upgrade(0, 0, 1000)
        self.assertIsNone(empty["expected_resin"])
        self.assertIsNone(empty["resin_for_50pct"])
        self.assertEqual(empty["probability_within_horizon"], 0)
        certain = estimate_first_upgrade(1, 50, 1000)
        self.assertEqual(certain["expected_resin"], 20)
        self.assertEqual(certain["resin_for_50pct"], 20)
        self.assertEqual(certain["resin_for_90pct"], 20)
        self.assertEqual(certain["expected_gain_per_upgrade"], 50)

    def test_invalid_inputs(self):
        for args in ((-0.1, 1, 1), (1.1, 1, 1), (0.5, -1, 1), (0.5, 1, -1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                estimate_first_upgrade(*args)


if __name__ == "__main__":
    unittest.main()
