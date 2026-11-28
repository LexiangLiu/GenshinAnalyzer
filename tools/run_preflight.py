from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str]) -> dict:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def main() -> int:
    checks = {
        "full_tests": _run([
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(ROOT / "tests"),
            "-v",
        ]),
        "benchmark": _run([
            sys.executable,
            "tools/benchmark_growth.py",
            "--repeats",
            "1",
        ]),
        "diff_check": _run([
            "git",
            "--no-pager",
            "diff",
            "--check",
        ]),
        "worktree": _run([
            "git",
            "--no-pager",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ]),
    }
    failed = [
        name
        for name, check in checks.items()
        if name in ("full_tests", "benchmark", "diff_check")
        and check["returncode"] != 0
    ]
    print(json.dumps({
        "schema_version": 1,
        "status": "passed" if not failed else "failed",
        "failed_checks": failed,
        "checks": checks,
        "publish_actions": "not_requested",
    }, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
