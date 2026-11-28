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
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-8000:],
    }


def main() -> int:
    preflight = _run([sys.executable, "tools/run_preflight.py"])
    status = _run([
        "git",
        "--no-pager",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ])
    branch = _run([
        "git",
        "--no-pager",
        "branch",
        "--show-current",
    ])
    external_blockers = [
        {
            "item": "real_account_sync",
            "status": "blocked_external_authorization",
            "reason": "no reliable source or credentials",
        },
        {
            "item": "live_activity_fetch",
            "status": "blocked_external_authorization",
            "reason": "no verified live source or credentials",
        },
        {
            "item": "merge_push_publish_deploy",
            "status": "blocked_explicit_authorization",
            "reason": "local repository work only",
        },
    ]
    passed = preflight["returncode"] == 0
    report = {
        "schema_version": 1,
        "status": "ready_for_review" if passed else "failed_preflight",
        "branch": branch["stdout"].strip(),
        "worktree": status["stdout"],
        "preflight": preflight,
        "external_blockers": external_blockers,
        "release_actions": "not_requested",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
