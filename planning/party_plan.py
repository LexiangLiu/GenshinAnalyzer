"""Multi-character planning: joint loadouts plus shared-domain farming value.

The joint optimizer decides who wears what.  This module then asks, for every
candidate domain, what a single additional five-star drop is worth to each
member, and aggregates those per-member expectations into a domain ranking.

A drop can only be equipped by one character, so the weighted sum of
per-member expected gains is an upper bound on the realized party gain.  The
conservative single-best-member value is reported alongside it; the default
ranking mode is ``sum`` because it reflects total opportunity across a roster.
"""

from __future__ import annotations

from math import isfinite, prod
from typing import Sequence

from core.models import Artifact
from core.resin import estimate_first_upgrade
from damage.build_objective import character_weapon_panel
from damage.character_models import catalog_metadata
from damage.damage_models import farming_substats
from data.game_data import DOMAINS, MAIN_VALUES, SETS, SUB_VALUES, lookup
from optimization.loadout_optimizer import LoadoutPiece, evaluate_loadout
from optimization.party_optimizer import PartyMember, optimize_party
from .farming import analyze_farming


_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")


def auto_main_stats(model) -> dict[str, list[str]]:
    """A sensible main-stat shortlist from the stats a model actually reads."""
    relevant = model.relevant_stats
    result = {"FLOWER": ["HP"], "PLUME": ["ATK"]}
    sands = [stat for stat in ("HP_PERCENT", "ATK_PERCENT", "DEF_PERCENT", "ENERGY_RECHARGE", "ELEMENTAL_MASTERY") if stat in relevant]
    result["SANDS"] = sands or ["HP_PERCENT", "ATK_PERCENT", "DEF_PERCENT", "ENERGY_RECHARGE", "ELEMENTAL_MASTERY"]
    goblet = [stat for stat in ("HP_PERCENT", "ATK_PERCENT", "DEF_PERCENT", "ELEMENTAL_MASTERY") if stat in relevant]
    for stat in ("PYRO_DMG_BONUS", "HYDRO_DMG_BONUS", "CRYO_DMG_BONUS", "ELECTRO_DMG_BONUS",
                 "ANEMO_DMG_BONUS", "GEO_DMG_BONUS", "DENDRO_DMG_BONUS", "PHYSICAL_DMG_BONUS"):
        if stat in relevant:
            goblet.insert(0, stat)
    result["GOBLET"] = goblet or ["ATK_PERCENT"]
    circlet = [stat for stat in ("CRIT_RATE", "CRIT_DMG") if stat in relevant]
    circlet += [stat for stat in ("HP_PERCENT", "ATK_PERCENT", "DEF_PERCENT", "ELEMENTAL_MASTERY") if stat in relevant]
    result["CIRCLET"] = circlet or ["CRIT_RATE", "CRIT_DMG"]
    return result


def _normalize_main_stats(value: dict | None, model) -> dict | None:
    if value is None:
        return auto_main_stats(model)
    if not isinstance(value, dict):
        raise ValueError("candidate_main_stats 必须是部位到主词条列表的对象")
    result = {}
    for slot, stats in value.items():
        if slot not in _SLOTS or not isinstance(stats, (list, tuple)):
            raise ValueError(f"candidate_main_stats 的 {slot} 无效")
        if any(stat not in MAIN_VALUES for stat in stats):
            raise ValueError(f"candidate_main_stats 的 {slot} 含未知主词条")
        result[slot] = list(stats)
    return result


def _domain_ids(domain_query: Sequence[str | int] | None, members: Sequence[PartyMember],
                targets: Sequence[int | None]) -> list[int]:
    if domain_query is not None:
        return [int(lookup(DOMAINS, domain)["id"]) for domain in domain_query]
    wanted = {target for target in targets if target is not None}
    if not wanted:
        return sorted(DOMAINS)
    result = [
        int(domain["id"]) for domain in DOMAINS.values()
        if wanted & set(domain["set_ids"])
    ]
    return result or sorted(DOMAINS)


def _member_bonus(member: PartyMember) -> dict:
    return dict(member.model.panel_bonus_stats if member.bonus_stats is None else member.bonus_stats)


def compact_main_stats(model) -> dict[str, list[str]]:
    """One standard main stat per slot for fast, end-game-style planning."""
    auto = auto_main_stats(model)
    return {
        "FLOWER": ["HP"],
        "PLUME": ["ATK"],
        "SANDS": auto["SANDS"][:1],
        "GOBLET": auto["GOBLET"][:1],
        "CIRCLET": auto["CIRCLET"][:1],
    }


def farming_value_for_member(
    member: PartyMember,
    artifacts: Sequence[Artifact],
    target: int | None,
    domain_id: int,
    *,
    threshold_pct: float,
    main_stats: dict | None,
    resin_per_claim: int,
) -> dict:
    """Value of one drop from ``domain_id`` for a member's fixed loadout."""
    base_stats, _ = character_weapon_panel(member.character, member.weapon, _member_bonus(member))
    baseline = float(evaluate_loadout(list(artifacts), member.model, base_stats).damage)
    result = analyze_farming(
        list(artifacts),
        objective=member.model.scaled_expression(),
        character=dict(member.character),
        weapon=dict(member.weapon) if member.weapon is not None else None,
        bonus_stats=_member_bonus(member),
        domains=[domain_id],
        drops=1,
        target_four_piece_set=target,
        report_substats=(),
        tracked_substats=farming_substats(member.model),
        main_stats=main_stats,
        min_upgrade_pct=threshold_pct,
        score_function=member.model.evaluate_increases,
        summary_only=True,
    )
    domain = result["domains_by_expected_gain"][0]
    overall = domain["overall"]
    probability = float(overall["single_drop_upgrade_probability"])
    gain = float(overall["expected_gain"])
    gain_pct = float(overall["expected_gain_pct"]) if overall.get("expected_gain_pct") is not None else 0.0
    metrics = estimate_first_upgrade(
        probability, gain, baseline, resin_per_claim=resin_per_claim,
    )
    best_slot = domain["slots_by_upgrade_probability"][0] if domain["slots_by_upgrade_probability"] else None
    by_slot = {
        slot: {
            "probability_per_drop": float(row["single_drop_upgrade_probability"]),
            "expected_gain": float(row["expected_gain"]),
            "expected_gain_pct": float(row["expected_gain_pct"]) if row.get("expected_gain_pct") is not None else 0.0,
        }
        for slot, row in domain["slots"].items()
    }
    return {
        "name": member.label,
        "character": member.model.character,
        "model": member.model.key,
        "weight": float(member.weight),
        "baseline_damage": baseline,
        "probability_per_drop": probability,
        "expected_gain": gain,
        "expected_gain_pct": gain_pct,
        "expected_resin": metrics["expected_resin"],
        "resin_for_50pct": metrics["resin_for_50pct"],
        "resin_for_90pct": metrics["resin_for_90pct"],
        "best_slot": best_slot,
        "by_slot": by_slot,
        "accuracy": member.model.assumptions.get("calibration", {}).get("accuracy")
        if isinstance(member.model.assumptions.get("calibration"), dict) else None,
    }


def rank_domains_for_loadouts(
    members: Sequence[PartyMember],
    loadouts: Sequence[Sequence[LoadoutPiece | Artifact]],
    targets: Sequence[int | None],
    domains: Sequence[str | int] | None = None,
    *,
    main_stats: Sequence[dict | None] | None = None,
    threshold_pct: float = 1.0,
    resin_per_claim: int = 20,
    score_mode: str = "sum",
) -> tuple[list[dict], int | None, list[str]]:
    """Rank domains for fixed loadouts without re-optimizing the artifacts.

    ``plan_party`` calls this after its joint search.  Farming loops call it
    between rounds, where re-running the full optimizer would be wasteful.
    """
    if not members:
        raise ValueError("至少需要一个角色")
    if len(loadouts) != len(members):
        raise ValueError("loadouts 必须与角色数量一致")
    if len(targets) != len(members):
        raise ValueError("targets 必须与角色数量一致")
    if type(threshold_pct) not in (int, float) or not isfinite(threshold_pct) or threshold_pct < 0:
        raise ValueError("threshold_pct 必须是有限的非负数")
    if type(resin_per_claim) is not int or resin_per_claim <= 0:
        raise ValueError("resin_per_claim 必须是正整数")
    if score_mode not in ("sum", "max"):
        raise ValueError("score_mode 只能是 sum 或 max")
    if main_stats is not None and len(main_stats) != len(members):
        raise ValueError("main_stats 必须与角色数量一致")

    domain_ids = list(dict.fromkeys(_domain_ids(domains, members, targets)))
    mains = [
        _normalize_main_stats(
            main_stats[index] if main_stats is not None else None, members[index].model,
        )
        for index in range(len(members))
    ]

    domain_rows = []
    warnings = []
    for domain_id in domain_ids:
        domain = DOMAINS[domain_id]
        member_rows = []
        for index, member in enumerate(members):
            try:
                row = farming_value_for_member(
                    member,
                    list(loadouts[index]),
                    targets[index],
                    domain_id,
                    threshold_pct=threshold_pct,
                    main_stats=mains[index],
                    resin_per_claim=resin_per_claim,
                )
            except ValueError as exc:
                row = {
                    "name": member.label,
                    "character": member.model.character,
                    "model": member.model.key,
                    "weight": float(member.weight),
                    "error": str(exc),
                    "probability_per_drop": 0.0,
                    "expected_gain": 0.0,
                    "expected_gain_pct": 0.0,
                    "expected_resin": None,
                }
                warnings.append(f"{domain['name']} 对 {member.label} 无法评估：{exc}")
            member_rows.append(row)

        probabilities = [row.get("probability_per_drop", 0.0) for row in member_rows]
        any_probability = 1.0 - prod(1.0 - p for p in probabilities) if probabilities else 0.0
        total_weight = sum(member.weight for member in members)
        weighted_gain_pct = sum(
            member.weight * row.get("expected_gain_pct", 0.0)
            for member, row in zip(members, member_rows)
        ) / total_weight
        weighted_gain = sum(
            member.weight * row.get("expected_gain", 0.0)
            for member, row in zip(members, member_rows)
        ) / total_weight
        best_member_gain_pct = max(
            (row.get("expected_gain_pct", 0.0) for row in member_rows), default=0.0,
        )
        score_pct = best_member_gain_pct if score_mode == "max" else weighted_gain_pct
        domain_rows.append({
            "domain_id": int(domain["id"]),
            "domain_name": domain["name"],
            "drop_sets": [
                {"id": set_id, "name": SETS[set_id]["name"]} for set_id in domain["set_ids"]
            ],
            "score_mode": score_mode,
            "score_pct_per_100_resin": score_pct * 100.0 / resin_per_claim,
            "weighted_expected_gain_pct": weighted_gain_pct,
            "weighted_expected_gain": weighted_gain,
            "best_member_expected_gain_pct": best_member_gain_pct,
            "probability_any_member_upgrade": any_probability,
            "expected_resin_to_any_upgrade": (
                resin_per_claim / any_probability if any_probability > 0 else None
            ),
            "members": member_rows,
        })

    domain_rows.sort(key=lambda row: row["score_pct_per_100_resin"], reverse=True)
    recommended = next(
        (row["domain_id"] for row in domain_rows if row["score_pct_per_100_resin"] > 0),
        None,
    )
    if recommended is None:
        warnings.append("当前阈值下没有任何秘境的单件掉落能提升队伍伤害。")
    return domain_rows, recommended, warnings


def plan_party(
    pieces: Sequence[LoadoutPiece | Artifact],
    members: Sequence[PartyMember],
    *,
    domains: Sequence[str | int] | None = None,
    threshold_pct: float = 1.0,
    current: Sequence[Sequence[LoadoutPiece | Artifact | None] | None] | None = None,
    candidate_main_stats: Sequence[dict | None] | None = None,
    max_nodes: int = 2_000_000,
    restarts: int = 4,
    seed: int = 0,
    max_iterations: int = 8,
    normalize: bool = True,
    resin_per_claim: int = 20,
    score_mode: str = "sum",
) -> dict:
    """Jointly equip every member and rank domains by marginal farming value."""
    if type(threshold_pct) not in (int, float) or not isfinite(threshold_pct) or threshold_pct < 0:
        raise ValueError("threshold_pct 必须是有限的非负数")
    if type(resin_per_claim) is not int or resin_per_claim <= 0:
        raise ValueError("resin_per_claim 必须是正整数")
    if score_mode not in ("sum", "max"):
        raise ValueError("score_mode 只能是 sum 或 max")
    if current is not None and len(current) != len(members):
        raise ValueError("current 必须与角色数量一致")
    if candidate_main_stats is not None and len(candidate_main_stats) != len(members):
        raise ValueError("candidate_main_stats 必须与角色数量一致")

    optimized = optimize_party(
        pieces, members,
        max_nodes=max_nodes, restarts=restarts, seed=seed,
        max_iterations=max_iterations, normalize=normalize,
    )
    assignments = list(optimized.members)
    targets = [assignment.target_four_piece_set for assignment in assignments]
    mains = [
        _normalize_main_stats(candidate_main_stats[index] if candidate_main_stats is not None else None,
                              members[index].model)
        for index in range(len(members))
    ]
    domain_rows, recommended, rank_warnings = rank_domains_for_loadouts(
        members,
        [assignment.loadout.artifacts() for assignment in assignments],
        targets,
        domains,
        main_stats=mains,
        threshold_pct=threshold_pct,
        resin_per_claim=resin_per_claim,
        score_mode=score_mode,
    )
    warnings = list(optimized.warnings) + rank_warnings

    current_rows = []
    if current is not None:
        for index, assignment in enumerate(assignments):
            if current[index] is None:
                current_rows.append(None)
                continue
            base_stats, _ = character_weapon_panel(
                members[index].character, members[index].weapon, _member_bonus(members[index]),
            )
            current_loadout = evaluate_loadout(list(current[index]), members[index].model, base_stats)
            delta = assignment.loadout.damage - current_loadout.damage
            current_rows.append({
                "name": members[index].label,
                "damage": current_loadout.damage,
                "delta": delta,
                "delta_pct": delta / current_loadout.damage * 100 if current_loadout.damage > 0 else None,
                "loadout": current_loadout.describe(),
            })

    member_output = []
    for index, assignment in enumerate(assignments):
        described = assignment.describe(normalize)
        described["current"] = current_rows[index] if current_rows else None
        described["candidate_main_stats"] = mains[index]
        member_output.append(described)

    return {
        "catalog": catalog_metadata(),
        "members": member_output,
        "total_objective": optimized.total_objective,
        "normalize": normalize,
        "search": {
            "exact": optimized.exact,
            "iterations": optimized.iterations,
            "conflicts": optimized.conflicts,
            "unused_count": len(optimized.unused),
        },
        "unused": [
            {"id": piece.artifact_id, "slot": piece.artifact.slot, "set_id": piece.artifact.set_id}
            for piece in optimized.unused
        ],
        "domains": domain_rows,
        "recommended_domain_id": recommended,
        "warnings": warnings,
    }
