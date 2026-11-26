"""Weapon + artifact joint optimization experiment on the simulated account.

Uses the realistic account produced by ``tools/simulate_account.py`` and four
characters whose weapon types overlap:

* 阿蕾奇诺 / 雷电将军 -- polearms compete for four polearms;
* 芙宁娜 / 神里绫华 -- swords compete for four swords.

For every character/weapon pair the catalog model is specialized with the
weapon's base stats, substat and a curated passive approximation.  The tool
then chooses one unique weapon per character, jointly optimizes artifacts, and
optionally ranks farming domains for the resulting party.

    python tools/weapon_experiment.py

Outputs ``weapon-report.json`` and ``weapon-report.md`` next to the account.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from artifact_sim import (  # noqa: E402
    Inventory, LoadoutPiece, PartyMember, compact_main_stats, get_damage_model,
    plan_party,
)
from artifact_sim.data.game_data import SETS  # noqa: E402
from artifact_sim.optimization.loadout_optimizer import evaluate_loadout  # noqa: E402
from artifact_sim.optimization.weapon_optimizer import (  # noqa: E402
    WeaponMember, WeaponOption, evaluate_member_weapon,
    optimize_weapons_and_artifacts,
)


ACCOUNT = ROOT / "examples" / "simulated-account"
ROSTER = ["Arlecchino", "Raiden Shogun", "Furina", "Kamisato Ayaka"]
POLE = "WEAPON_POLE"
SWORD = "WEAPON_SWORD_ONE_HAND"
ALL = frozenset({"normal", "charged", "plunge", "skill", "burst"})

WEAPONS = [
    WeaponOption(
        "Crimson Moon's Semblance", POLE, refinement=1,
        panel_bonus={"ATK_PERCENT": 15.0}, dmg_bonus=36.0,
        notes="精一：攻击力+15%，生命之契≥30% 时增伤+36%",
    ),
    WeaponOption(
        "Engulfing Lightning", POLE, refinement=1,
        panel_bonus={"ATK_PERCENT": 28.0},
        notes="精一：按充能折算攻击力，实验按约 200% 充能给 28% 攻击力",
    ),
    WeaponOption(
        '"The Catch"', POLE, refinement=5,
        dmg_bonus=32.0, applies_to=frozenset({"burst"}),
        notes="精五：元素爆发增伤+32%，仅对爆发节点生效",
    ),
    WeaponOption(
        "Deathmatch", POLE, refinement=5,
        panel_bonus={"ATK_PERCENT": 32.0},
        notes="精五：按少敌场景近似攻击力+32%",
    ),
    WeaponOption(
        "Splendor of Tranquil Waters", SWORD, refinement=1,
        panel_bonus={"HP_PERCENT": 16.0}, dmg_bonus=24.0,
        notes="精一：生命值+16%，生命变化后增伤按满层近似+24%",
    ),
    WeaponOption(
        "Mistsplitter Reforged", SWORD, refinement=1,
        dmg_bonus=24.0,
        notes="精一：元素伤害加成按两层近似+24%",
    ),
    WeaponOption(
        "Amenoma Kageuchi", SWORD, refinement=5,
        notes="精五：主要提供回能，未计增伤",
    ),
    WeaponOption(
        "Wolf-Fang", SWORD, refinement=5,
        dmg_bonus=32.0, applies_to=frozenset({"skill", "burst"}),
        notes="精五：战技/爆发增伤+32%（实验近似）",
    ),
]


def load_account(path: Path) -> tuple[dict, Inventory, list[LoadoutPiece]]:
    account = json.loads((path / "account.json").read_text(encoding="utf-8"))
    inventory = Inventory(path / "account.sqlite3")
    pieces = [
        LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
        for artifact_id, artifact in inventory.artifacts()
    ]
    return account, inventory, pieces


def current_member(
    character: str,
    account: dict,
    inventory: Inventory,
) -> tuple[WeaponMember, WeaponOption, list[LoadoutPiece]]:
    row = next(item for item in account["roster"] if item["character"] == character)
    weapon = next(item for item in WEAPONS if item.name == row["weapon"])
    model = get_damage_model(character)
    if model.target_four_piece_set is None:
        raise ValueError(f"{character} 没有目标四件套")
    candidates = [item for item in WEAPONS if item.weapon_type == row["weapon_type"]]
    current_pieces = [
        LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
        for artifact_id, artifact in inventory.equipped_artifacts(character)
    ]
    member = WeaponMember(
        character={"id": character, "level": 90, "ascension": 6},
        base_model=model,
        candidate_weapons=candidates,
        target_four_piece_set=model.target_four_piece_set,
        name=row.get("name_zh") or character,
        current_weapon=weapon,
        current_pieces=current_pieces,
    )
    return member, weapon, current_pieces


def _markdown(report: dict) -> str:
    lines = [
        "# 武器 + 圣遗物联合优化实验",
        "",
        f"- 角色：{'、'.join(item['name'] for item in report['members'])}",
        f"- 圣遗物库存：{report['account']['artifact_count']} 件五星",
        f"- 武器库存：{report['account']['weapon_count']} 把，候选 {len(report['public_weapons'])} 把",
        f"- 联合搜索：`exact={report['joint_search']['exact']}`，冲突 {report['joint_search']['conflicts']}，武器交换 {report['weapon_swaps']} 次",
        f"- 队伍总伤害：{report['totals']['current']:.0f} → {report['totals']['optimal']:.0f}（{report['totals']['gain_pct']:+.2f}%）",
        "",
        "## 当前 → 武器与圣遗物联合最优",
        "",
        "| 角色 | 当前武器 | 最优武器 | 当前伤害 | 最优伤害 | 提升 | 四件套 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in report["members"]:
        gain = item["gain_pct"]
        gain_text = "-" if gain is None else f"{gain:+.2f}%"
        target = item["target_four_piece_set"]["name"] if item["target_four_piece_set"] else "-"
        lines.append(
            f"| {item['name']} | {item['current_weapon'] or '-'} | {item['weapon']['name']} | "
            f"{item['current_damage'] or 0:.0f} | {item['damage']:.0f} | {gain_text} | {target} |",
        )
    lines += [
        "",
        "## 武器分配",
        "",
        "| 角色 | 武器 | 独立评估伤害 | 圣遗物套装 |",
        "| --- | --- | ---: | --- |",
    ]
    for item in report["members"]:
        lines.append(
            f"| {item['name']} | {item['weapon']['name']} | {item['independent_damage']:.0f} | "
            f"{'、'.join(item['set_names'])} |",
        )
    if report.get("unused_weapons"):
        lines += ["", "未使用武器：" + "、".join(item["name"] for item in report["unused_weapons"])]
    lines += [
        "",
        "## 每角色武器候选",
        "",
    ]
    for item in report["members"]:
        lines += [
            f"### {item['name']}",
            "",
            "| 武器 | 独立评估伤害 | 说明 |",
            "| --- | ---: | --- |",
        ]
        for score in item["weapon_scores"]:
            lines.append(
                f"| {score['weapon']} | {score['independent_damage']:.0f} | {score['passive']} |",
            )
        lines.append("")
    if report.get("domains"):
        lines += [
            "## 武器+圣遗物最优后的副本收益",
            "",
            "| 排名 | 秘境 | 加权期望提升 | 单角色最高 | 任一角色提升概率 |",
            "| ---: | --- | ---: | ---: | ---: |",
        ]
        for rank, domain in enumerate(report["domains"], 1):
            lines.append(
                f"| {rank} | {domain['domain_name']} | {domain['weighted_expected_gain_pct']:.3f}% | "
                f"{domain['best_member_expected_gain_pct']:.3f}% | "
                f"{domain['probability_any_member_upgrade']:.1%} |",
            )
    lines += [
        "",
        "## 说明",
        "",
        "- 武器被动按固定精炼和固定场景近似，不是完整 Pando 模拟；实验重点是验证“武器唯一+圣遗物唯一”的二维分配流程。",
        "- 武器基础攻击、副属性由本地 7.2 数据提供；被动增伤/攻击力为近似覆盖值。",
        "- 独立评估使用全部圣遗物，随后联合分配解决武器和圣遗物争用。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="武器+圣遗物联合优化实验")
    parser.add_argument("--account", type=Path, default=ACCOUNT)
    parser.add_argument("--max-nodes", type=int, default=80_000)
    parser.add_argument("--no-plan", action="store_true", help="不运行副本收益推荐")
    args = parser.parse_args()

    account, inventory, pieces = load_account(args.account)
    members = []
    current = []
    for character in ROSTER:
        member, weapon, current_pieces = current_member(character, account, inventory)
        members.append(member)
        current.append(current_pieces)
    print(f"[weapons] evaluating {len(members)} characters x {len(WEAPONS)} weapons ...")
    result = optimize_weapons_and_artifacts(
        pieces, members,
        max_nodes=args.max_nodes, restarts=1, max_iterations=1,
        normalize=False,
    )

    current_damage = {}
    for member in members:
        loadout, _ = evaluate_member_weapon(member, member.current_weapon, member.current_pieces)
        current_damage[member.label] = loadout.damage
    choices = tuple(
        replace(choice, current_damage=current_damage.get(choice.member.label))
        for choice in result.members
    )
    described = [choice.describe() for choice in choices]
    total_current = sum(current_damage.values())
    total_optimal = sum(choice.damage for choice in choices)
    warnings = list(result.warnings)
    if total_current > 0 and total_optimal < total_current:
        warnings.append("绝对伤害最优没有超过当前武器配置；请把结果视为武器重分配的负例。")
    report = {
        "account": {
            "artifact_count": len(pieces),
            "weapon_count": len(WEAPONS),
            "roster": ROSTER,
        },
        "totals": {
            "current": total_current,
            "optimal": total_optimal,
            "gain_pct": (total_optimal / total_current - 1.0) * 100.0 if total_current > 0 else None,
        },
        "public_weapons": [weapon.describe() for weapon in result.public_weapons],
        "members": described,
        "assignment": {choice.member.label: choice.weapon.name for choice in choices},
        "unused_weapons": [weapon.describe() for weapon in result.unused_weapons],
        "joint_search": result.joint.describe()["search"],
        "weapon_swaps": result.weapon_swaps,
        "warnings": warnings,
        "domains": [],
    }

    if not args.no_plan:
        print("[plan] ranking domains for the weapon+artifact optimized party ...")
        party_members = [
            PartyMember(
                model=choice.model,
                character=choice.member.character,
                weapon=choice.weapon.as_panel(),
                weight=choice.member.weight,
                target_four_piece_set=choice.member.resolved_set(),
                name=choice.member.label,
            )
            for choice in choices
        ]
        mains = [compact_main_stats(choice.model) for choice in choices]
        plan = plan_party(
            pieces, party_members,
            domains=[1151, 361, 982, 221],
            threshold_pct=1.0,
            current=current,
            candidate_main_stats=mains,
            max_nodes=args.max_nodes,
            restarts=0,
            max_iterations=1,
        )
        report["domains"] = plan["domains"]
        report["recommended_domain_id"] = plan["recommended_domain_id"]
        report["plan_warnings"] = plan["warnings"]

    (args.account / "weapon-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (args.account / "weapon-report.md").write_text(_markdown(report), encoding="utf-8")
    print(
        f"[done] party total {total_current:.0f} -> {total_optimal:.0f} "
        f"({(total_optimal / total_current - 1.0) * 100.0:+.2f}%)" if total_current > 0 else "[done]",
    )
    for item in described:
        gain = item["gain_pct"]
        print(
            f"  {item['name']}: {item['current_weapon']} -> {item['weapon']['name']} "
            f"{item['damage']:.0f} ({gain:+.2f}%)" if gain is not None else
            f"  {item['name']}: {item['weapon']['name']} {item['damage']:.0f}",
        )
    if report.get("recommended_domain_id"):
        print(f"  recommended domain: {report['recommended_domain_id']}")
    print(f"[files] {args.account / 'weapon-report.md'}")


if __name__ == "__main__":
    main()
