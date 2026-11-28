from pathlib import Path
import unittest
from unittest.mock import patch
import io
import json
from contextlib import redirect_stdout


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

    def test_release_checklist_reports_external_blockers_without_publish_actions(self):
        from tools import release_checklist

        def fake_run(command):
            if "run_preflight.py" in command:
                return {
                    "command": command,
                    "returncode": 0,
                    "stdout": "passed",
                    "stderr": "",
                }
            if "branch" in command:
                return {
                    "command": command,
                    "returncode": 0,
                    "stdout": "main\n",
                    "stderr": "",
                }
            return {
                "command": command,
                "returncode": 0,
                "stdout": "",
                "stderr": "",
            }

        with patch.object(release_checklist, "_run", side_effect=fake_run):
            output = io.StringIO()
            with redirect_stdout(output):
                result = release_checklist.main()
        report = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "ready_for_review")
        self.assertEqual(report["release_actions"], "not_requested")
        self.assertEqual(
            {item["item"] for item in report["external_blockers"]},
            {
                "real_account_sync",
                "live_activity_fetch",
                "merge_push_publish_deploy",
            },
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
