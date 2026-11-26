"""Simulate repeated artifact farming and refresh the joint recommendation.

The tool starts from the realistic account in ``examples/simulated-account``,
copies its database, then for several rounds:

1. ranks candidate domains for the current weapon + artifact builds;
2. farms the recommended (or a fixed) domain and enhances the five-star drops;
3. re-runs the two-dimensional weapon + artifact optimizer;
4. reports the new builds and the next domain recommendation.

    python tools/farming_loop.py
    python tools/farming_loop.py --rounds 5 --drops-per-round 10
    python tools/farming_loop.py --fixed-domain 982 --no-equip

Outputs ``farming-report.json`` and ``farming-report.md`` plus a copy of the
farmed database in the output directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from artifact_sim import Inventory, simulate_farming_loop  # noqa: E402

try:
    from weapon_experiment import ACCOUNT, ROSTER, load_account, current_member
except ImportError:  # pragma: no cover - when imported as tools.farming_loop
    from tools.weapon_experiment import ACCOUNT, ROSTER, load_account, current_member


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2f}%"


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"


def _substats(values: dict) -> str:
    return "、".join(f"{stat}+{value:g}" for stat, value in values.items()) or "-"


def _markdown(report: dict, output: Path) -> str:
    meta = report["meta"]
    final = report["final"]
    lines = [
        "# 自动刷取与配装更新模拟",
        "",
        f"- 账号：`{meta['account']}`",
        f"- 角色：{'、'.join(meta['roster'])}",
        f"- 轮次：{meta['rounds']} 轮 × 每轮 {meta['drops_per_round']} 件五星",
        f"- 累计掉落：{report['total_drops']} 件，折合 {report['total_resin']} 树脂",
        f"- 掉落强化：+{meta['enhance_to']}",
        f"- 停止原因：{report['stopped_reason']}",
        f"- 基线伤害：{_number(report['baseline_damage'])}",
        f"- 最终伤害：{_number(final['damage'])}（{_pct(final['gain_pct_vs_baseline'])}）",
        f"- 下一步推荐秘境：{final['recommended_domain_id'] or '-'}",
        "",
        "## 每轮刷取结果",
        "",
        "| 轮次 | 秘境 | 掉落 | 实际装备 | 刷前伤害 | 刷后伤害 | 较上轮 | 较基线 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["rounds"]:
        domain = row["domain"]["name"] if row["domain"] else "-"
        lines.append(
            f"| {row['round']} | {domain} | {len(row['drops'])} | "
            f"{len(row['used_drops'])} | {row['before_damage']:.0f} | "
            f"{row['after_damage']:.0f} | {_pct(row['gain_pct_vs_previous'])} | "
            f"{_pct(row['gain_pct_vs_baseline'])} |",
        )

    lines += [
        "",
        "## 最终武器与圣遗物",
        "",
        "| 角色 | 武器 | 伤害 | 较基线 | 四件套 | 圣遗物 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for member in final["members"]:
        target = member["target_four_piece_set"]["name"] if member["target_four_piece_set"] else "-"
        lines.append(
            f"| {member['name']} | {member['weapon']['name']} | {member['damage']:.0f} | "
            f"{_pct(member['gain_pct'])} | {target} | {'、'.join(member['set_names'])} |",
        )

    lines += [
        "",
        "## 下一步副本收益",
        "",
        "| 排名 | 秘境 | 加权期望提升 | 单角色最高 | 任一角色提升概率 |",
        "| ---: | --- | ---: | ---: | ---: |",
    ]
    for rank, domain in enumerate(final["domain_ranking"], 1):
        lines.append(
            f"| {rank} | {domain['domain_name']} | "
            f"{domain['weighted_expected_gain_pct']:.3f}% | "
            f"{domain['best_member_expected_gain_pct']:.3f}% | "
            f"{domain['probability_any_member_upgrade']:.1%} |",
        )

    used = [
        (row["round"], drop)
        for row in report["rounds"]
        for drop in row["used_drops"]
    ]
    lines += ["", "## 新掉落中被采用的圣遗物", ""]
    if used:
        lines += [
            "| 轮次 | 套装 | 部位 | 主词条 | 等级 | 副词条 | 评分 |",
            "| ---: | --- | --- | --- | ---: | --- | ---: |",
        ]
        for round_index, drop in used:
            lines.append(
                f"| {round_index} | {drop['set_name']} | {drop['piece_name']} | "
                f"{drop['main_stat']} {drop['main_value']:.1f} | +{drop['level']} | "
                f"{_substats(drop['substats'])} | {drop['score']:.1f} |",
            )
    else:
        lines.append("所有模拟掉落均未进入本轮最优配装。")

    lines += [
        "",
        "## 说明",
        "",
        "- 武器被动仍为固定精炼、固定场景的近似值，具体武器排名不应视为完整 Pando 结论。",
        "- 只模拟五星掉落，每件按一次 20 树脂结算；强化等级由 `--enhance-to` 控制。",
        "- 每轮副本排名基于当轮配装计算固定配装的单件替换收益，随后用新掉落重跑武器+圣遗物联合优化。",
        "- 第 1 轮的刷后伤害同时包含“当前配装 → 联合最优”和掉落两部分；后续轮次才单独反映新掉落的影响。",
        "- 默认只写回新掉落；加 `--equip` 才会把每轮最优配装写回模拟数据库的角色档案。",
        "- 联合搜索在库存较大时可能达到节点上限，结果属于启发式最优。",
        "",
        f"完整数据：`{(output / 'farming-report.json').name}`；模拟后的数据库：`{(output / 'farming-account.sqlite3').name}`。",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="自动模拟刷取圣遗物并更新配装推荐")
    parser.add_argument("--account", type=Path, default=ACCOUNT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--drops-per-round", type=int, default=8)
    parser.add_argument("--domains", default="1151,361,982,221",
                        help="候选秘境 ID，逗号分隔")
    parser.add_argument("--fixed-domain", type=int, default=None,
                        help="固定刷取某个秘境，而不是跟随推荐")
    parser.add_argument("--enhance-to", type=int, default=20)
    parser.add_argument("--max-nodes", type=int, default=30_000)
    parser.add_argument("--restarts", type=int, default=1)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261127)
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument("--target-gain", type=float, default=None)
    parser.add_argument("--equip", action="store_true",
                        help="把每轮最优配装写回模拟数据库的角色档案")
    args = parser.parse_args()

    output = args.output or (args.account / "farming-loop")
    output.mkdir(parents=True, exist_ok=True)
    source_db = args.account / "account.sqlite3"
    if not source_db.exists():
        raise SystemExit(f"找不到账号数据库：{source_db}")
    target_db = output / "farming-account.sqlite3"
    shutil.copy2(source_db, target_db)

    account = json.loads((args.account / "account.json").read_text(encoding="utf-8"))
    inventory = Inventory(target_db)
    members = [current_member(character, account, inventory)[0] for character in ROSTER]
    domains = [int(item.strip()) for item in args.domains.split(",") if item.strip()]

    print(
        f"[loop] {len(members)} characters, {args.rounds} rounds x "
        f"{args.drops_per_round} drops, domains={domains}",
    )
    result = simulate_farming_loop(
        inventory,
        members,
        rounds=args.rounds,
        drops_per_round=args.drops_per_round,
        domains=domains,
        fixed_domain=args.fixed_domain,
        enhance_to=args.enhance_to,
        max_nodes=args.max_nodes,
        restarts=args.restarts,
        max_iterations=args.max_iterations,
        seed=args.seed,
        normalize=False,
        threshold_pct=args.threshold,
        resin_per_claim=20,
        score_mode="sum",
        target_gain_pct=args.target_gain,
        equip_best=args.equip,
    )
    report = result.describe()
    report["meta"] = {
        "account": str(args.account),
        "roster": [member.label for member in members],
        "rounds": args.rounds,
        "drops_per_round": args.drops_per_round,
        "domains": domains,
        "fixed_domain": args.fixed_domain,
        "enhance_to": args.enhance_to,
        "max_nodes": args.max_nodes,
        "seed": args.seed,
        "equip": args.equip,
    }
    (output / "farming-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (output / "farming-report.md").write_text(_markdown(report, output), encoding="utf-8")

    print(f"[baseline] {result.baseline_damage:.0f}")
    for row in result.rounds:
        print(
            f"  round {row.index}: {row.domain_name} drops={len(row.drops)} "
            f"used={len(row.used_new_artifact_ids)} "
            f"{row.before_damage:.0f} -> {row.after_damage:.0f}",
        )
    gain = (
        (result.final_damage / result.baseline_damage - 1.0) * 100.0
        if result.baseline_damage > 0 else None
    )
    print(
        f"[done] {result.final_damage:.0f} ({_pct(gain)}), "
        f"next domain: {result.recommended_domain_id}, stop: {result.stopped_reason}",
    )
    print(f"[files] {output}")


if __name__ == "__main__":
    main()
