"""Generate a realistic simulated account and run end-to-end loadout planning.

The generated account contains:

* an owned-character roster with levels, constellations, talents and weapons;
* a five-star artifact inventory with realistic set/slot/main-stat/substat and
  enhancement-level distributions;
* a plausible "current" build for every invested character, obtained from an
  earlier, smaller artifact pool;
* a joint multi-character optimization and a domain farming recommendation.

Outputs are written to ``examples/simulated-account`` by default:

* ``account.sqlite3`` -- full artifact inventory and current profiles;
* ``account.json`` -- roster, weapons, inventory summary and current profiles;
* ``party-request.json`` -- reusable request for ``party plan``;
* ``report.json`` -- full optimization and recommendation report;
* ``summary.json`` -- compact metrics;
* ``report.md`` -- human-readable summary.

Example:

    python tools/simulate_account.py --seed 20261126

The run is intentionally configurable.  A real end-game account often owns
40-60 characters and 1,000-2,000 five-star artifacts; joint optimization is
performed for the invested roster (12 by default) because every member shares
the same artifact pool.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from artifact_sim import (  # noqa: E402
    Inventory, LoadoutPiece, PartyMember, Simulator, compact_main_stats,
    get_damage_model, optimize_loadout, plan_party,
)
from genshin_simulator.damage.character_models import catalog_metadata  # noqa: E402
from genshin_simulator.data.game_data import DATA, DOMAINS, SETS  # noqa: E402


REFERENCE = ROOT / "genshin_simulator" / "data" / "reference_data"
DEFAULT_OUTPUT = ROOT / "examples" / "simulated-account"
SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")
LEVEL_WEIGHTS = {
    0: 0.25, 4: 0.15, 8: 0.10, 12: 0.12, 16: 0.13, 20: 0.25,
}

# A realistic end-game roster: several elements, weapon types and popular
# damage dealers.  Target sets come from the character models.
MAIN_ROSTER = [
    {"character": "Neuvillette", "weapon": "Tome of the Eternal Flow", "name": "那维莱特"},
    {"character": "Arlecchino", "weapon": "Crimson Moon's Semblance", "name": "阿蕾奇诺"},
    {"character": "Mavuika", "weapon": "A Thousand Blazing Suns", "name": "玛薇卡"},
    {"character": "Furina", "weapon": "Splendor of Tranquil Waters", "name": "芙宁娜"},
    {"character": "Nahida", "weapon": "A Thousand Floating Dreams", "name": "纳西妲"},
    {"character": "Alhaitham", "weapon": "Light of Foliar Incision", "name": "艾尔海森"},
    {"character": "Raiden Shogun", "weapon": "Engulfing Lightning", "name": "雷电将军"},
    {"character": "Kamisato Ayaka", "weapon": "Mistsplitter Reforged", "name": "神里绫华"},
    {"character": "Ganyu", "weapon": "Amos' Bow", "name": "甘雨"},
    {"character": "Xiao", "weapon": "Primordial Jade Winged-Spear", "name": "魈"},
    {"character": "Yelan", "weapon": "Aqua Simulacra", "name": "夜兰"},
    {"character": "Navia", "weapon": "Verdict", "name": "娜维娅"},
]

# Domains that drop the invested roster's target sets, plus their neighbours.
DEFAULT_DOMAINS = [982, 1151, 1190, 507, 361, 221, 516, 1142]


def _load_gzip(name: str) -> dict:
    with gzip.open(REFERENCE / name, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[key] for key in keys])[0]


def _weapon_pool(weapons: dict, weapon_type: str, rarity: int) -> list[str]:
    return sorted(
        record["name"] for record in weapons.values()
        if record.get("weaponType") == weapon_type and record.get("rarity") == rarity
    )


def _build_roster(seed: int, owned_count: int, invested_count: int) -> tuple[list[dict], dict[int, int]]:
    rng = random.Random(seed)
    characters = _load_gzip("characters.en.json.gz")
    characters_zh = _load_gzip("characters.zh.json.gz")
    weapons = _load_gzip("weapons.en.json.gz")
    names = {
        key: record["name"] for key, record in characters.items()
        if key not in ("aether", "lumine", "manekin", "manekina")
    }
    by_name = {record["name"]: record for record in characters.values()}
    zh_by_key = {
        key: characters_zh[key]["name"] for key in characters if key in characters_zh
    }
    known = [name for name in (row["character"] for row in MAIN_ROSTER) if name in by_name]
    if len(known) < invested_count:
        raise ValueError(f"主阵容只有 {len(known)} 个角色，少于 invested={invested_count}")

    invested = MAIN_ROSTER[:invested_count]
    owned = [row["character"] for row in invested]
    remaining = [
        name for name in by_name
        if name not in owned and by_name[name]["name"] in names.values()
    ]
    rng.shuffle(remaining)
    for name in remaining:
        if len(owned) >= owned_count:
            break
        owned.append(name)

    roster = []
    for name in owned:
        record = by_name[name]
        is_invested = any(row["character"] == name for row in invested)
        weapon_type = record.get("weaponType", "")
        if is_invested:
            row = next(item for item in invested if item["character"] == name)
            weapon = row["weapon"]
            level, constellation, talents = 90, 0, (10, 10, 10)
            refinement = 1
        else:
            candidates = _weapon_pool(weapons, weapon_type, 4) or _weapon_pool(weapons, weapon_type, 3)
            if not candidates:
                raise ValueError(f"{name} 没有可用武器")
            weapon = rng.choice(candidates)
            level = rng.choice((20, 40, 50, 60, 70, 80))
            constellation = rng.randint(0, 6 if record.get("rarity") == 4 else 2)
            talents = tuple(rng.randint(1, 8) for _ in range(3))
            refinement = rng.randint(1, 5)
        key = next((key for key, value in characters.items() if value["name"] == name), None)
        roster.append({
            "character": name,
            "name_zh": next((item["name"] for item in MAIN_ROSTER if item["character"] == name),
                            zh_by_key.get(key, record.get("name"))),
            "element": record.get("elementType"),
            "rarity": record.get("rarity"),
            "weapon_type": weapon_type,
            "weapon": weapon,
            "weapon_refinement": refinement,
            "level": level,
            "constellation": constellation,
            "talents": {"auto": talents[0], "skill": talents[1], "burst": talents[2]},
            "invested": is_invested,
        })
    return roster, {record["name"]: record["id"] for record in by_name.values()}


def _weapon_inventory(roster: list[dict], seed: int) -> list[dict]:
    rng = random.Random(seed + 1)
    weapons = _load_gzip("weapons.en.json.gz")
    result = []
    for row in roster:
        result.append({
            "name": row["weapon"],
            "owner": row["character"],
            "level": 90 if row["invested"] else row["level"],
            "refinement": row["weapon_refinement"],
            "weapon_type": row["weapon_type"],
        })
    spare_types = sorted({row["weapon_type"] for row in roster})
    for _ in range(18):
        weapon_type = rng.choice(spare_types)
        rarity = rng.choice((4, 4, 4, 5))
        pool = _weapon_pool(weapons, weapon_type, rarity)
        if not pool:
            continue
        result.append({
            "name": rng.choice(pool),
            "owner": None,
            "level": rng.choice((1, 20, 70, 90)),
            "refinement": rng.randint(1, 5 if rarity == 4 else 1),
            "weapon_type": weapon_type,
        })
    return result


def _artifact_set_pool(members: list[PartyMember]) -> tuple[list[int], list[int]]:
    target_sets = sorted({member.model.target_four_piece_set for member in members
                          if member.model.target_four_piece_set is not None})
    extra = [
        set_id for set_id in SETS
        if 5 in SETS[set_id]["rarities"] and set_id not in target_sets
    ]
    return target_sets, extra


def _generate_artifacts(
    sim: Simulator,
    rng: random.Random,
    count: int,
    target_sets: list[int],
    extra_sets: list[int],
) -> list:
    set_weights = {}
    for set_id in target_sets:
        set_weights[set_id] = 0.7 / max(1, len(target_sets))
    for set_id in extra_sets:
        set_weights[set_id] = 0.3 / max(1, len(extra_sets))
    artifacts = []
    for _ in range(count):
        set_id = rng.choices(list(set_weights), weights=[set_weights[key] for key in set_weights])[0]
        slot = rng.choices(SLOTS, weights=[DATA["slot_weights"][item] for item in SLOTS])[0]
        main_weights = DATA["main_stat_weights"][slot]
        main = rng.choices(list(main_weights), weights=[main_weights[key] for key in main_weights])[0]
        artifact = sim.create(set_id, slot=slot, main_stat=main)
        level = rng.choices(list(LEVEL_WEIGHTS), weights=[LEVEL_WEIGHTS[key] for key in LEVEL_WEIGHTS])[0]
        if level:
            artifact = sim.enhance(artifact, level)
        artifacts.append(artifact)
    return artifacts


def _members(roster: list[dict], invested_count: int) -> list[PartyMember]:
    rows = [row for row in roster if row["invested"]][:invested_count]
    result = []
    for row in rows:
        model = get_damage_model(
            row["character"],
            weapon=row["weapon"],
            level=90,
            ascension=6,
            constellation=0,
            talents={"auto": 10, "skill": 10, "burst": 10},
            weapon_level=90,
            weapon_ascension=6,
            refinement=1,
            crit_mode="average",
        )
        result.append(PartyMember(
            model=model,
            character={"id": row["character"], "level": 90, "ascension": 6},
            weapon={"id": row["weapon"], "level": 90, "ascension": 6},
            weight=1.0,
            target_four_piece_set=model.target_four_piece_set,
            name=row["name_zh"],
        ))
    return result


def _identity(piece: LoadoutPiece) -> str:
    return str(piece.artifact_id) if piece.artifact_id is not None else f"object:{id(piece.artifact)}"


def _current_builds(
    pieces: list[LoadoutPiece],
    members: list[PartyMember],
    rng: random.Random,
    *,
    max_nodes: int,
) -> tuple[list[list[LoadoutPiece]], dict]:
    """Build plausible current loadouts from an earlier fraction of the pool.

    A player usually equips characters one at a time from the pieces they have
    already farmed, so a sequential greedy assignment is a closer model than a
    joint optimum.  It is also much faster than running the full joint search
    twice.
    """
    attempts = []
    panels = [member.panel() for member in members]
    for fraction in (0.6, 0.75, 0.9):
        subset_count = max(len(pieces) // 3, int(len(pieces) * fraction))
        subset = rng.sample(pieces, min(subset_count, len(pieces)))
        available = list(subset)
        selected_all: list[list[LoadoutPiece]] = []
        feasible = True
        for member, panel in zip(members, panels):
            try:
                result = optimize_loadout(
                    available, member.model, panel,
                    required_four_piece_set=member.resolved_set(),
                    max_nodes=max_nodes,
                )
            except ValueError:
                feasible = False
                break
            selected = [piece for piece in result.loadout.pieces if piece is not None]
            used = {_identity(piece) for piece in selected}
            available = [piece for piece in available if _identity(piece) not in used]
            selected_all.append(selected)
        attempts.append({
            "fraction": fraction,
            "feasible": feasible,
            "subset_size": len(subset),
        })
        if feasible:
            return selected_all, {
                "attempts": attempts, "subset_size": len(subset),
                "strategy": "sequential-greedy",
            }

    # Fallback: use the full pool and the same greedy order.
    available = list(pieces)
    selected_all = []
    for member, panel in zip(members, panels):
        result = optimize_loadout(
            available, member.model, panel,
            required_four_piece_set=member.resolved_set(),
            max_nodes=max_nodes,
        )
        selected = [piece for piece in result.loadout.pieces if piece is not None]
        used = {_identity(piece) for piece in selected}
        available = [piece for piece in available if _identity(piece) not in used]
        selected_all.append(selected)
    attempts.append({"fraction": 1.0, "feasible": True, "subset_size": len(pieces)})
    return selected_all, {
        "attempts": attempts, "subset_size": len(pieces),
        "strategy": "sequential-greedy-full",
    }


def _set_names(described_pieces: list[dict]) -> list[str]:
    return [piece["set_name"] for piece in described_pieces if piece.get("set_name")]


def _summary(report: dict) -> dict:
    members = []
    for member in report["members"]:
        current = member.get("current")
        members.append({
            "name": member["name"],
            "character": member["character"],
            "target_set": member["target_four_piece_set"],
            "current_damage": current["damage"] if current else None,
            "optimal_damage": member["damage"],
            "gain_pct": current["delta_pct"] if current else None,
            "normalized_damage": member["normalized_damage"],
            "set_names": _set_names(member["loadout"]["pieces"]),
        })
    domains = []
    for domain in report["domains"]:
        domains.append({
            "domain_id": domain["domain_id"],
            "domain_name": domain["domain_name"],
            "score_pct_per_100_resin": domain["score_pct_per_100_resin"],
            "weighted_expected_gain_pct": domain["weighted_expected_gain_pct"],
            "best_member_expected_gain_pct": domain["best_member_expected_gain_pct"],
            "probability_any_member_upgrade": domain["probability_any_member_upgrade"],
            "expected_resin_to_any_upgrade": domain["expected_resin_to_any_upgrade"],
            "members": [
                {
                    "name": row["name"],
                    "probability_per_drop": row.get("probability_per_drop", 0.0),
                    "expected_gain_pct": row.get("expected_gain_pct", 0.0),
                    "expected_resin": row.get("expected_resin"),
                    "best_slot": row.get("best_slot"),
                }
                for row in domain["members"]
            ],
        })
    return {
        "recommended_domain_id": report["recommended_domain_id"],
        "search": report["search"],
        "members": members,
        "domains": domains,
        "warnings": report["warnings"],
    }


def _markdown(summary: dict, account: dict, output: Path) -> str:
    invested = account['meta']['invested_characters']
    optimized = account['meta'].get('optimized_characters', invested)
    lines = [
        "# 模拟账号配装与刷取推荐",
        "",
        f"- 种子：`{account['meta']['seed']}`",
        f"- 角色库：{account['meta']['owned_characters']} 个角色，其中 {invested} 个已投入资源、{optimized} 个参与本轮联合优化",
        f"- 圣遗物：{account['meta']['artifact_count']} 件五星，+20 共 {account['meta']['level_counts'].get('20', 0)} 件",
        f"- 武器库：{account['meta']['weapon_count']} 把",
        f"- 模型目录：{account['meta']['catalog_models']} 个标定模型",
        "",
    ]
    if optimized < invested:
        lines += [
            f"其余 {invested - optimized} 个投入角色保留当前配装，未参与本轮共享库存分配。",
            "",
        ]
    lines += [
        "## 当前配装与联合最优",
        "",
        "| 角色 | 目标四件套 | 当前伤害 | 最优伤害 | 提升 |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in summary["members"]:
        gain = "-" if row["gain_pct"] is None else f"{row['gain_pct']:+.2f}%"
        current = "-" if row["current_damage"] is None else f"{row['current_damage']:.0f}"
        target = row["target_set"]["name"] if row["target_set"] else "-"
        lines.append(f"| {row['name']} | {target} | {current} | {row['optimal_damage']:.0f} | {gain} |")
    lines += [
        "",
        "## 副本收益排名",
        "",
        "| 排名 | 秘境 | 加权期望提升 | 单角色最高 | 任一角色提升概率 | 期望树脂 | 每 100 树脂分数 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, domain in enumerate(summary["domains"], 1):
        resin = "-" if domain["expected_resin_to_any_upgrade"] is None else f"{domain['expected_resin_to_any_upgrade']:.0f}"
        lines.append(
            f"| {rank} | {domain['domain_name']} | {domain['weighted_expected_gain_pct']:.3f}% | "
            f"{domain['best_member_expected_gain_pct']:.3f}% | "
            f"{domain['probability_any_member_upgrade']:.1%} | {resin} | "
            f"{domain['score_pct_per_100_resin']:.2f} |",
        )
    recommended = next(
        (domain for domain in summary["domains"] if domain["domain_id"] == summary["recommended_domain_id"]),
        None,
    )
    if recommended is not None:
        lines += [
            "",
            f"推荐秘境：**{recommended['domain_name']}**。各角色在该秘境的单件提升：",
            "",
            "| 角色 | 提升概率 | 期望增幅 | 期望树脂 | 最佳部位 |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for row in recommended["members"]:
            resin = "-" if row["expected_resin"] is None else f"{row['expected_resin']:.0f}"
            lines.append(
                f"| {row['name']} | {row['probability_per_drop']:.3%} | "
                f"{row['expected_gain_pct']:.3f}% | {resin} | {row['best_slot'] or '-'} |",
            )
    lines += [
        "",
        "## 说明",
        "",
        "- 联合优化使用全部背包圣遗物；目标四件套按模型默认。",
        "- “当前配装”由较早的 60% 背包池按角色顺序配装得到，用于模拟已经投入过资源的账号。",
        "- 多角色期望增幅之和是共享掉落场景的上界，单角色最高值是保守下界。",
        "- 目录模型是单段、无反应、无队伍增益的个人伤害模型；武器特效仅覆盖固定武器模型。",
        "",
        f"完整数据：`{output.name}/report.json`；可用于重跑的请求：`{output.name}/party-request.json`。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="生成拟真原神账号并运行整体配装与刷本推荐")
    parser.add_argument("--seed", type=int, default=20261126)
    parser.add_argument("--characters", type=int, default=36, help="角色库规模")
    parser.add_argument("--invested", type=int, default=12, help="参与联合优化的角色数")
    parser.add_argument("--artifacts", type=int, default=1500, help="五星圣遗物数量")
    parser.add_argument("--domains", default=",".join(str(item) for item in DEFAULT_DOMAINS),
                        help="候选秘境 ID，逗号分隔")
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument("--max-nodes", type=int, default=150_000)
    parser.add_argument("--restarts", type=int, default=0)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--full-main-stats", action="store_true",
                        help="不压缩候选主词条；更精确但更慢")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-plan", action="store_true", help="只生成账号和当前配装，不运行副本推荐")
    args = parser.parse_args()

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    roster, _ = _build_roster(args.seed, max(args.characters, args.invested), args.invested)
    invested_rows = [row for row in roster if row["invested"]][:args.invested]
    if len(invested_rows) < args.invested:
        raise ValueError("主阵容数量不足")
    members = _members(roster, args.invested)
    target_sets, extra_sets = _artifact_set_pool(members)
    print(f"[account] roster={len(roster)} invested={len(members)} artifacts={args.artifacts}")

    sim = Simulator(args.seed)
    rng = random.Random(args.seed + 2)
    artifacts = _generate_artifacts(sim, rng, args.artifacts, target_sets, extra_sets)
    db_path = output / "account.sqlite3"
    if db_path.exists():
        db_path.unlink()
    inventory = Inventory(db_path)
    ids = inventory.add(artifacts)
    pieces = [LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
              for artifact_id, artifact in zip(ids, artifacts)]
    print(f"[inventory] stored {len(pieces)} artifacts in {db_path}")

    current, current_meta = _current_builds(
        pieces, members, rng, max_nodes=args.max_nodes,
    )
    for member, selected in zip(members, current):
        inventory.equip(member.model.character, [piece.artifact_id for piece in selected])
    print(f"[current] built {len(current)} profiles from {current_meta.get('subset_size')} artifacts")

    domains = [int(item.strip()) for item in args.domains.split(",") if item.strip()]
    candidate_stats = (
        [None] * len(members) if args.full_main_stats
        else [compact_main_stats(member.model) for member in members]
    )
    report = None
    summary = None
    if not args.skip_plan:
        print(f"[plan] optimizing {len(members)} characters across {len(domains)} domains ...")
        report = plan_party(
            pieces, members,
            domains=domains,
            threshold_pct=args.threshold,
            current=current,
            candidate_main_stats=candidate_stats,
            max_nodes=args.max_nodes,
            restarts=args.restarts,
            max_iterations=args.max_iterations,
            normalize=True,
            resin_per_claim=20,
            score_mode="sum",
        )
        summary = _summary(report)

    level_counts = Counter(str(artifact.level) for artifact in artifacts)
    set_counts = Counter(SETS[artifact.set_id]["name"] for artifact in artifacts)
    account = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seed": args.seed,
            "game_version": DATA["game_version"],
            "catalog_models": catalog_metadata()["count"],
            "owned_characters": len(roster),
            "invested_characters": len(members),
            "artifact_count": len(artifacts),
            "weapon_count": len(_weapon_inventory(roster, args.seed)),
            "level_counts": dict(level_counts),
            "set_counts": dict(set_counts.most_common()),
        },
        "roster": roster,
        "weapons": _weapon_inventory(roster, args.seed),
        "invested": [
            {
                "character": member.model.character,
                "name": member.label,
                "weapon": row["weapon"],
                "target_four_piece_set": member.target_four_piece_set,
            }
            for member, row in zip(members, invested_rows)
        ],
        "current_profiles": {
            member.model.character: [piece.artifact_id for piece in selected]
            for member, selected in zip(members, current)
        },
        "current_build_meta": current_meta,
        "domains": [
            {"id": domain_id, "name": DOMAINS[domain_id]["name"],
             "sets": [SETS[set_id]["name"] for set_id in DOMAINS[domain_id]["set_ids"]]}
            for domain_id in domains
        ],
        "files": {
            "database": "account.sqlite3",
            "report": "report.json",
            "summary": "summary.json",
        },
    }
    request = {
        "members": [
            {
                "character": member.model.character,
                "weapon": {"id": member.weapon["id"], "level": 90, "ascension": 6},
                "target_four_piece_set": member.target_four_piece_set,
                "profile": member.model.character,
                "name": member.label,
                "weight": 1.0,
                "candidate_main_stats": candidate_stats[index],
            }
            for index, member in enumerate(members)
        ],
        "domains": domains,
        "threshold_pct": args.threshold,
        "max_nodes": args.max_nodes,
        "restarts": args.restarts,
        "max_iterations": args.max_iterations,
        "normalize": True,
        "resin_per_claim": 20,
        "score_mode": "sum",
    }

    (output / "account.json").write_text(
        json.dumps(account, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (output / "party-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if report is not None and summary is not None:
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (output / "report.md").write_text(_markdown(summary, account, output), encoding="utf-8")

        print(f"[done] recommended domain: {summary['recommended_domain_id']}")
        for domain in summary["domains"][:5]:
            print(
                f"  {domain['domain_name']}: score={domain['score_pct_per_100_resin']:.3f} "
                f"sum={domain['weighted_expected_gain_pct']:.3f}% "
                f"best={domain['best_member_expected_gain_pct']:.3f}% "
                f"p_any={domain['probability_any_member_upgrade']:.1%}",
            )
    else:
        print("[skip] planning disabled; account and current profiles were written")
    print(f"[files] {output}")


if __name__ == "__main__":
    main()
