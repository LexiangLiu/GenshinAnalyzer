from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
_CORE_PACKAGES = (
    "core",
    "data",
    "damage",
    "inventory",
    "optimization",
    "planning",
)


class ArchitectureCompatibilityTests(unittest.TestCase):
    def test_domain_packages_do_not_import_http_or_acceptance_ui(self):
        forbidden = (
            "tools.acceptance_ui",
            "http.server",
            "webbrowser",
            "acceptance_ui",
        )
        for package in _CORE_PACKAGES:
            for path in (ROOT / package).rglob("*.py"):
                content = path.read_text(encoding="utf-8")
                for dependency in forbidden:
                    self.assertNotIn(
                        dependency,
                        content,
                        f"{path.relative_to(ROOT)} imports forbidden UI dependency {dependency}",
                    )

    def test_public_compatibility_facade_and_new_packages_import(self):
        import artifact_sim
        import account
        import activities
        import applications
        import goals
        import planning_v2
        import plans
        import resources
        self.assertTrue(hasattr(artifact_sim, "Inventory"))
        self.assertTrue(hasattr(account, "AccountSnapshot"))
        self.assertTrue(hasattr(applications, "PlanningService"))
        self.assertTrue(hasattr(planning_v2, "calculate_growth_plan"))


if __name__ == "__main__":
    unittest.main()
