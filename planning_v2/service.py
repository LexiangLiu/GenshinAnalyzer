from __future__ import annotations

from typing import Mapping

from account.models import AccountSnapshot
from activities.evaluation import evaluate_activity_rules
from damage.character_models import catalog_character_coverage, catalog_coverage_audit
from goals.models import AccountGoal
from resources.aggregation import (
    aggregate_account_requirement,
    aggregate_character_requirement,
)


def _action_status(requirement, relevant_unknown: list[dict]) -> str:
    if relevant_unknown:
        return "blocked_unknown"
    if requirement.lines:
        if any(line.remaining is not None and line.remaining > 0 for line in requirement.lines):
            return "cost_remaining"
        return "ready"
    return "unknown"


def _build_actions(goal: AccountGoal, snapshot: AccountSnapshot, inventory: Mapping[str, object] | None) -> list[dict]:
    states = {state.identity: state for state in snapshot.characters}
    actions = []
    for character_goal in goal.characters:
        state = states.get(character_goal.character_id)
        if state is None:
            actions.append({
                "action_type": "character_missing",
                "character": character_goal.character_id,
                "cost": {"known": False},
                "benefit": {"expected_gain": "unknown"},
                "dependencies": [],
                "feasibility": "blocked_unknown",
                "horizon": "both",
                "priority": character_goal.short_term_priority,
                "risks": {"interval": "unknown", "reason": "账号快照中不存在该角色"},
            })
            continue
        requirement = aggregate_character_requirement(
            state,
            character_goal,
            inventory=inventory,
        )
        unknown_by_field = {
            item["field"]: item
            for item in requirement.unknown
        }
        build = character_goal.build

        if character_goal.target_level is not None and character_goal.target_level > state.level:
            relevant = [
                item for field, item in unknown_by_field.items()
                if field == "character_ascension"
            ]
            actions.append({
                "action_type": "character_ascension",
                "character": character_goal.character_id,
                "target_level": character_goal.target_level,
                "cost": requirement.to_dict()["lines"],
                "benefit": {"target_level": character_goal.target_level, "expected_gain": "unknown"},
                "dependencies": ["offline_character_level_cost"],
                "feasibility": _action_status(requirement, relevant),
                "horizon": "short",
                "priority": character_goal.short_term_priority,
                "risks": {"interval": "unknown", "reason": "面板收益未由本行动单独计算"},
            })

        for slot in ("auto", "skill", "burst"):
            target = (character_goal.target_talents or {}).get(slot)
            current = state.talents.get(slot)
            if type(target) is int and type(current) is int and target > current:
                relevant = [
                    item for field, item in unknown_by_field.items()
                    if field == f"talent_{slot}"
                ]
                actions.append({
                    "action_type": "talent_upgrade",
                    "character": character_goal.character_id,
                    "slot": slot,
                    "from_level": current,
                    "to_level": target,
                    "cost": requirement.to_dict()["lines"],
                    "benefit": {"target_talent": target, "expected_gain": "unknown"},
                    "dependencies": ["character_ascension"],
                    "feasibility": _action_status(requirement, relevant),
                    "horizon": "short",
                    "priority": character_goal.short_term_priority,
                    "risks": {"interval": "unknown", "reason": "天赋收益需由公式覆盖和伤害模型确认"},
                })

        if build.weapon_id is not None and build.weapon_target_level is not None:
            weapon_change = build.weapon_id != state.weapon_id
            level_change = build.weapon_target_level != (state.weapon_level or 1)
            if weapon_change or level_change:
                relevant = [
                    item for field, item in unknown_by_field.items()
                    if field == "weapon"
                ]
                actions.append({
                    "action_type": "weapon_upgrade",
                    "character": character_goal.character_id,
                    "target_weapon": build.weapon_id,
                    "target_level": build.weapon_target_level,
                    "cost": requirement.to_dict()["lines"],
                    "benefit": {"target_weapon": build.weapon_id, "target_level": build.weapon_target_level, "expected_gain": "unknown"},
                    "dependencies": ["weapon_access"],
                    "feasibility": _action_status(requirement, relevant),
                    "horizon": "long",
                    "priority": character_goal.long_term_priority,
                    "risks": {"interval": "unknown", "reason": "武器面板收益需由现有伤害引擎重算"},
                })

        if build.four_piece_set is not None:
            actions.append({
                "action_type": "artifact_farming",
                "character": character_goal.character_id,
                "target_four_piece_set": build.four_piece_set,
                "target_piece_count": 5,
                "cost": {"resin": "unknown", "mora": "unknown", "inventory": "unknown"},
                "benefit": {"target_loadout": "unknown", "expected_gain": "unknown"},
                "dependencies": ["existing_shared_inventory_optimizer", "domain_configuration"],
                "feasibility": "unknown",
                "horizon": "long",
                "priority": character_goal.long_term_priority,
                "risks": {
                    "interval": "unknown",
                    "reason": "未运行 Monte Carlo；区间和搜索限制尚未计算",
                },
            })

    actions.append({
        "action_type": "weekly_boss",
        "character": None,
        "cost": {"resin": "unknown", "weekly_limit": "unknown"},
        "benefit": {"talent_or_trounce_drop": "unknown"},
        "dependencies": ["source_snapshot"],
        "feasibility": "blocked_unknown",
        "horizon": "short",
        "priority": 0.0,
        "risks": {"interval": "unknown", "reason": "周本配置与收益未提供"},
    })
    return actions


def calculate_growth_plan(
    goal: AccountGoal | Mapping[str, object],
    snapshot: AccountSnapshot | Mapping[str, object],
    *,
    inventory: Mapping[str, object] | None = None,
    engine_results: Mapping[str, object] | None = None,
) -> dict:
    """Minimum growth-plan projection using existing data and optimizer boundaries."""
    if isinstance(goal, Mapping):
        goal = AccountGoal.from_dict(goal)
    if isinstance(snapshot, Mapping):
        snapshot = AccountSnapshot.from_dict(snapshot)

    requirement = aggregate_account_requirement(
        goal,
        snapshot,
        inventory=inventory,
    )
    character_rows = []
    for character_goal in goal.characters:
        coverage = catalog_character_coverage(character_goal.character_id)
        character_rows.append({
            "character": character_goal.character_id,
            "short_term_priority": character_goal.short_term_priority,
            "long_term_priority": character_goal.long_term_priority,
            "formula_coverage": coverage,
        })
    actions = _build_actions(goal, snapshot, inventory)
    activity_evaluation = evaluate_activity_rules(goal.activity_rules)
    for accepted in activity_evaluation["accepted"]:
        rule = accepted["rule"]
        actions.append({
            "action_type": "activity",
            "character": None,
            "activity_id": rule["rule_id"],
            "cost": dict(rule.get("benefits", {}).get("cost", {})),
            "benefit": dict(rule.get("benefits", {}).get("reward", {})),
            "dependencies": [rule["source_ref"]],
            "feasibility": "ready",
            "horizon": "short",
            "priority": float(rule["confidence"]),
            "risks": {
                "interval": "unknown",
                "reason": "活动收益来自已验证配置，不等同于实时官方数据",
            },
        })
    for degraded in activity_evaluation["degraded"]:
        rule = degraded["rule"]
        actions.append({
            "action_type": "activity",
            "character": None,
            "activity_id": rule["rule_id"],
            "cost": {"known": False},
            "benefit": {"activity_reward": "unknown"},
            "dependencies": [rule["source_ref"]],
            "feasibility": "blocked_unknown",
            "horizon": "short",
            "priority": 0.0,
            "risks": {
                "interval": "unknown",
                "reason": degraded["reason"],
            },
        })
    routes = {
        horizon: sorted(
            [
                action for action in actions
                if action["horizon"] in (horizon, "both")
            ],
            key=lambda action: (
                action["feasibility"] not in ("ready", "cost_remaining"),
                -float(action["priority"]),
            ),
        )
        for horizon in ("short", "long")
    }
    engine_results = dict(engine_results or {})
    loadout_result = engine_results.get("loadout")
    formula_result = engine_results.get("formula")
    expectation_result = engine_results.get("expectation")
    monte_carlo_result = engine_results.get("monte_carlo")
    engine_summary = {
        "loadout": loadout_result if isinstance(loadout_result, Mapping) else None,
        "formula": formula_result if isinstance(formula_result, Mapping) else None,
        "expectation": expectation_result if isinstance(expectation_result, Mapping) else None,
        "monte_carlo": monte_carlo_result if isinstance(monte_carlo_result, Mapping) else None,
        "errors": [
            dict(item)
            for item in (engine_results.get("errors") or ())
            if isinstance(item, Mapping)
        ],
    }
    monte_carlo_method = (
        monte_carlo_result.get("method")
        if isinstance(monte_carlo_result, Mapping)
        else None
    )
    monte_carlo_limitations = (
        monte_carlo_method.get("limitations")
        if isinstance(monte_carlo_method, Mapping)
        else []
    )
    expected_benefit = "unknown"
    if isinstance(monte_carlo_result, Mapping):
        tiers = monte_carlo_result.get("rankings")
        if isinstance(tiers, list) and tiers:
            expected_benefit = {"account_farming_ranking": tiers}
    elif isinstance(expectation_result, Mapping):
        expected_benefit = {
            "loadout_expectation": expectation_result.get("method", {}),
        }
    elif isinstance(loadout_result, Mapping):
        expected_benefit = {
            "loadout_objective": loadout_result.get("total_objective"),
        }
    return {
        "schema_version": 1,
        "goal": goal.to_dict(),
        "account_source": dict(snapshot.source),
        "resources": requirement.to_dict(),
        "characters": character_rows,
        "actions": actions,
        "routes": routes,
        "comparison": {
            "cost": requirement.to_dict(),
            "expected_benefit": expected_benefit,
            "risk": {
                "interval": (
                    monte_carlo_method.get("mean_interval")
                    if isinstance(monte_carlo_method, Mapping)
                    else "unknown"
                ),
                "search_limit": (
                    monte_carlo_method.get("maximum_searches")
                    if isinstance(monte_carlo_method, Mapping)
                    else "未运行配装搜索；节点限制不适用"
                ),
                "sampling_limit": (
                    monte_carlo_method.get("samples")
                    if isinstance(monte_carlo_method, Mapping)
                    else "未运行 Monte Carlo；抽样误差未知"
                ),
                "engine_limitations": (
                    list(monte_carlo_limitations)
                    if isinstance(monte_carlo_limitations, list)
                    else []
                ),
            },
            "missing": [dict(item) for item in requirement.unknown],
            "feasibility": (
                "blocked_unknown"
                if requirement.unknown
                else "requires_inventory_confirmation"
            ),
        },
        "activities": activity_evaluation,
        "confidence": {
            "status": "partial",
            "formula_reliability": "catalog_verified_or_not_calibrated",
            "resource_inventory": "unknown_without_user_snapshot",
            "activity": (
                "active_verified_rules_only"
                if activity_evaluation["accepted"]
                else "no_verified_active_benefit"
            ),
            "known_sources": ["data/reference_data"],
            "unknown_fields": [dict(item) for item in requirement.unknown],
        },
        "coverage": {
            "formula": {
                row["character"]: row["formula_coverage"]
                for row in character_rows
            },
            "catalog": catalog_coverage_audit(),
            "resource": "offline_reference_costs",
            "real_account": "not_imported",
            "activity": activity_evaluation,
        },
        "method": {
            "resource_aggregation": "offline_reference_cost_range_minus_inventory",
            "formula": "existing_damage_catalog_only",
            "loadout_search": "completed" if isinstance(loadout_result, Mapping) else "not_run",
            "formula_weighting": "completed" if isinstance(formula_result, Mapping) else "not_run",
            "expectation": "completed" if isinstance(expectation_result, Mapping) else "not_run",
            "monte_carlo": "completed" if isinstance(monte_carlo_result, Mapping) else "not_run",
            "search_node_limit": (
                loadout_result.get("search")
                if isinstance(loadout_result, Mapping)
                else "not_applicable"
            ),
            "sampling_limit": (
                monte_carlo_method
                if isinstance(monte_carlo_method, Mapping)
                else "not_applicable"
            ),
        },
        "engine_results": engine_summary,
        "limitations": [
            "摩拉与突破/天赋/武器物品来源来自离线图鉴，不代表实时账号库存。",
            "圣遗物需求未重新运行配装和秘境优化；树脂收益标记为 unknown。",
            "活动收益只来自已验证且在有效期内的结构化配置。",
            "未校准公式不会伪装成可评分结果。",
        ],
        "warnings": [
            dict(item) for item in requirement.unknown
        ] + [
            {"field": "activity", "reason": item["reason"], "rule_id": item["rule"]["rule_id"]}
            for item in activity_evaluation["degraded"]
        ] + [
            {
                "field": "engine",
                "reason": item.get("reason", "现有计算引擎执行失败"),
                "engine": item.get("engine", "unknown"),
            }
            for item in engine_summary["errors"]
        ],
    }
