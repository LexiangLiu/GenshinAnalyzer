"""Single-character planning: best loadout and expected resin to improve."""

from __future__ import annotations

from math import isfinite
from typing import Sequence

from .build_objective import character_weapon_panel
from .damage_models import DamageModel, farming_substats
from .farming import analyze_farming
from .game_data import SETS, lookup
from .loadout_optimizer import LoadoutPiece, evaluate_loadout, optimize_loadout
from .models import Artifact
from .resin import estimate_first_upgrade


def plan_single_character(
    pieces: Sequence[LoadoutPiece | Artifact | None],
    *,
    model: DamageModel,
    character: dict,
    weapon: dict | None = None,
    bonus_stats: dict | None = None,
    four_piece_set: str | int | None = None,
    domains: Sequence[str | int] | None = None,
    threshold_pct: float = 1.0,
    current: Sequence[LoadoutPiece | Artifact | None] | None = None,
    candidate_main_stats: dict | None = None,
    max_nodes: int = 2_000_000,
    resin_per_claim: int = 20,
) -> dict:
    """Plan one character's best artifact loadout and its next farm target.

    The loadout is optimized under the target four-piece set constraint. The
    farming estimate reuses the exact single-replacement distribution from
    :func:`farming.analyze_farming`, so it inherits its one-piece and +20
    candidate assumptions.
    """
    if type(threshold_pct) not in (int, float) or not isfinite(threshold_pct) or threshold_pct < 0:
        raise ValueError("threshold_pct 必须是有限的非负数")
    if type(resin_per_claim) is not int or resin_per_claim <= 0:
        raise ValueError("resin_per_claim 必须是正整数")
    target_query = four_piece_set if four_piece_set is not None else model.target_four_piece_set
    if target_query is None:
        raise ValueError("plan 需要目标四件套；请指定 four_piece_set 或在模型中提供默认值")
    target = lookup(SETS, target_query)
    if 5 not in target["rarities"] or "4" not in target["effects"]:
        raise ValueError("目标四件套必须是有四件套效果的五星套装")
    target_id = target["id"]
    if model.target_four_piece_set is not None and target_id != model.target_four_piece_set:
        raise ValueError(
            f"伤害模型 {model.key} 只覆盖套装 {model.target_four_piece_set} 的效果"
        )
    fixed_weapon = model.assumptions.get("weapon")
    if fixed_weapon is not None:
        if weapon is None:
            raise ValueError(f"伤害模型 {model.key} 需要武器「{fixed_weapon}」")
        from .character_models import weapon_matches
        if not weapon_matches(weapon.get("id"), fixed_weapon):
            raise ValueError(f"伤害模型 {model.key} 只覆盖武器「{fixed_weapon}」")
    bonus_stats = dict(model.panel_bonus_stats if bonus_stats is None else bonus_stats)
    base_stats, _ = character_weapon_panel(character, weapon, bonus_stats)
    optimization = optimize_loadout(
        pieces, model, base_stats, required_four_piece_set=target_id, max_nodes=max_nodes,
    )
    best = optimization.loadout
    equipped = list(best.artifacts())

    farming = analyze_farming(
        equipped,
        objective=model.scaled_expression(),
        character=character,
        weapon=weapon,
        bonus_stats=bonus_stats,
        domains=domains,
        drops=1,
        target_four_piece_set=target_id,
        # The plan reports probability/gain, not per-substat expectations.
        report_substats=(),
        tracked_substats=farming_substats(model),
        main_stats=candidate_main_stats,
        min_upgrade_pct=threshold_pct,
        score_function=model.evaluate_increases,
        summary_only=True,
    )
    baseline = float(farming["baseline"]["objective"])
    domain_results = []
    for domain in farming["domains_by_expected_gain"]:
        overall = domain["overall"]
        metrics = estimate_first_upgrade(
            overall["single_drop_upgrade_probability"],
            overall["expected_gain"],
            baseline,
            resin_per_claim=resin_per_claim,
        )
        slots = {}
        for slot, row in domain["slots"].items():
            slot_metrics = estimate_first_upgrade(
                row["single_drop_upgrade_probability"],
                row["expected_gain"],
                baseline,
                resin_per_claim=resin_per_claim,
            )
            slots[slot] = {
                "upgrade_probability": row["single_drop_upgrade_probability"],
                "expected_gain": row["expected_gain"],
                "expected_gain_pct": row["expected_gain_pct"],
                **slot_metrics,
            }
        best_slot = max(
            slots, key=lambda slot: slots[slot]["upgrade_probability"], default=None,
        )
        domain_results.append({
            "domain_id": domain["domain_id"],
            "domain_name": domain["domain_name"],
            "drop_sets": domain["drop_sets"],
            "best_slot": best_slot,
            "by_slot": slots,
            **metrics,
        })
    domain_results.sort(
        key=lambda item: item["expected_resin"] if item["expected_resin"] is not None else float("inf"),
    )
    recommended = domain_results[0]["domain_id"] if domain_results and domain_results[0]["expected_resin"] is not None else None

    current_result = None
    if current is not None:
        current_loadout = evaluate_loadout(current, model, base_stats)
        delta = best.damage - current_loadout.damage
        current_result = {
            "loadout": current_loadout.describe(),
            "damage_delta": delta,
            "damage_delta_pct": delta / current_loadout.damage * 100 if current_loadout.damage > 0 else None,
        }

    warnings = []
    if weapon is None:
        warnings.append("未提供武器；模型只使用角色白值，武器特效与副属性未计入。")
    selected_pieces = [piece for piece in best.pieces if piece is not None]
    if any(piece.artifact.level < 20 for piece in selected_pieces):
        warnings.append("最优配装包含未满级圣遗物；升级已有圣遗物是未纳入本计划的另一种行动。")
    if current_result is not None and any(
        piece.artifact.level < 20
        for piece in (item for item in current if item is not None)
    ):
        warnings.append("当前配装包含未满级圣遗物；与最优配装的伤害差可能包含升级收益。")
    if not optimization.exact:
        warnings.append("配装搜索达到节点上限，结果不是严格最优。")
    if any(domain.get("probability_per_drop", 0) == 0 for domain in domain_results):
        warnings.append("当前阈值下没有单件提升；可将 threshold 设为 0 查看任意正提升。")

    return {
        "warnings": warnings,
        "model": model.describe(),
        "scenario": {
            "character": character,
            "weapon": weapon,
            "bonus_stats": bonus_stats,
        },
        "target_four_piece_set": {"id": target_id, "name": target["name"]},
        "optimal_loadout": best.describe(),
        "current_loadout": current_result,
        "search": {
            "nodes": optimization.nodes,
            "exact": optimization.exact,
            "max_nodes": optimization.max_nodes,
        },
        "farming": {
            "threshold_pct": float(threshold_pct),
            "resin_per_claim": resin_per_claim,
            "baseline_damage": baseline,
            "recommended_domain_id": recommended,
            "domains": domain_results,
        },
    }
