from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
import sys
from pathlib import Path
from typing import Callable

from account import AccountSnapshot, CharacterState
from applications import PlanningService
from damage.character_models import catalog_coverage_audit
from goals import AccountGoal, BuildGoal, CharacterGoal


def _timed(operation: Callable[[], object], repeats: int) -> dict:
    durations = []
    for _ in range(repeats):
        started = time.perf_counter()
        operation()
        durations.append(time.perf_counter() - started)
    return {
        "repeats": repeats,
        "mean_seconds": statistics.mean(durations),
        "median_seconds": statistics.median(durations),
        "min_seconds": min(durations),
        "max_seconds": max(durations),
    }


def benchmark(repeats: int = 5) -> dict:
    snapshot = AccountSnapshot(
        source={"kind": "simulation", "reproducible_seed": 73},
        characters=[
            CharacterState(
                identity="Neuvillette",
                level=90,
                ascension=6,
                constellation=0,
                talents={"auto": 10, "skill": 10, "burst": 10},
                weapon_id="Tome of the Eternal Flow",
                weapon_level=90,
                weapon_refinement=1,
            )
        ],
        inventory={"artifact_count": 180},
        metadata={"missing_fields": ["real_account_inventory"]},
    )
    goal = AccountGoal(
        name="基准目标",
        horizon="mixed",
        characters=[
            CharacterGoal(
                character_id="Neuvillette",
                target_level=90,
                target_constellation=0,
                target_talents={"auto": 10, "skill": 10, "burst": 10},
                build=BuildGoal(
                    weapon_id="Tome of the Eternal Flow",
                    weapon_target_level=90,
                    four_piece_set=15031,
                ),
            )
        ],
        status="active",
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        service = PlanningService(root)
        snapshot_id = service.accounts.save(snapshot, "基准账号")
        goal_id = service.goals.save(goal)

        coverage = _timed(catalog_coverage_audit, repeats)
        planning = _timed(
            lambda: service.calculate_and_save(
                account_snapshot_id=snapshot_id,
                goal_id=goal_id,
                name="基准方案",
            ),
            repeats,
        )
        persistence = _timed(
            lambda: service.backup(root / "bench-backups"),
            repeats,
        )
    return {
        "schema_version": 1,
        "repeats": repeats,
        "environment": {
            "python_benchmark": "perf_counter",
            "hardware_sla": "not_applicable",
        },
        "operations": {
            "coverage_audit": coverage,
            "planning_calculate_and_save": planning,
            "persistence_backup": persistence,
        },
        "limitations": [
            "基准只反映本机和本次环境，不构成性能 SLA。",
            "配装、公式、期望和 Monte Carlo 未在此脚本中执行真实搜索。",
            "结果不包含网络、真实账号导入或实时活动来源。",
        ],
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="运行养成决策系统本地性能基准")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats 必须介于 1 和 20")
    print(json.dumps(benchmark(args.repeats), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
