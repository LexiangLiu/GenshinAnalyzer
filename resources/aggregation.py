from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Sequence

from data.references import get_character_profile, get_game_record

from .models import ResourceLine, ResourceRequirement


_LEVEL_ASCENSION = (
    (20, 1),
    (40, 2),
    (50, 3),
    (60, 4),
    (70, 5),
    (80, 6),
)


def _target_ascension(level: int) -> int:
    if type(level) is not int or level < 1:
        raise ValueError("目标等级必须为正整数")
    for threshold, stage in _LEVEL_ASCENSION:
        if level < threshold:
            return stage - 1
    return 6 if level <= 90 else 6


def _cost_entries(costs: Mapping[str, object], prefix: str, start: int, end: int) -> list[dict]:
    rows = []
    for stage in range(start + 1, end + 1):
        key = f"{prefix}{stage}"
        entries = costs.get(key, ())
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            rows.append({
                "resource_id": entry.get("id"),
                "name": str(entry.get("name") or entry.get("id")),
                "count": int(entry.get("count", 0)),
            })
    return rows


def _inventory_items(inventory: Mapping[str, object] | None) -> dict[str, int]:
    if not isinstance(inventory, Mapping):
        return {}
    raw = inventory.get("items", inventory)
    result = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if isinstance(value, Mapping):
                count = value.get("count")
            else:
                count = value
            if type(count) is int and count >= 0:
                result[str(key)] = count
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            count = item.get("count")
            key = item.get("id", item.get("name"))
            if key is not None and type(count) is int and count >= 0:
                result[str(key)] = count
    return result


def _merge_costs(entries: Sequence[Mapping[str, object]], inventory: Mapping[str, object] | None) -> list[ResourceLine]:
    totals = defaultdict(int)
    names = {}
    for entry in entries:
        resource_id = str(entry["resource_id"])
        totals[resource_id] += int(entry["count"])
        names[resource_id] = str(entry["name"])
    available = _inventory_items(inventory)
    lines = []
    for resource_id, count in sorted(totals.items()):
        remaining = max(0, count - available.get(resource_id, 0))
        if resource_id == "202":
            name = "摩拉"
        else:
            name = names[resource_id]
        lines.append(ResourceLine(
            resource_id=202 if resource_id == "202" else resource_id,
            name=name,
            count=count,
            source="data/reference_data",
            remaining=remaining,
        ))
    return lines


def aggregate_character_requirement(
    character_state,
    character_goal,
    *,
    inventory: Mapping[str, object] | None = None,
) -> ResourceRequirement:
    """Aggregate only costs present in the pinned offline reference snapshots."""
    identity = character_state.identity
    entries = []
    unknown = []
    actions = []

    try:
        profile = get_character_profile(identity, language="zh")
        character_record = profile["character"]
        character_costs = character_record.get("costs", {})
    except (KeyError, TypeError, ValueError, RuntimeError):
        character_costs = {}
        unknown.append({
            "field": "character_ascension",
            "character": identity,
            "reason": "离线角色突破费用快照不可用",
        })

    target_level = character_goal.target_level
    if target_level is not None:
        target_stage = _target_ascension(target_level)
        entries.extend(_cost_entries(
            character_costs,
            "ascend",
            character_state.ascension,
            target_stage,
        ))
    else:
        unknown.append({
            "field": "character_target_level",
            "character": identity,
            "reason": "未设置角色目标等级",
        })

    target_talents = character_goal.target_talents
    if target_talents:
        talent_record = next(iter(profile.get("talents", ())), {}) if "profile" in locals() else {}
        talent_costs = talent_record.get("costs", {})
        for slot in ("auto", "skill", "burst"):
            current = character_state.talents.get(slot)
            target = target_talents.get(slot)
            if type(current) is not int or type(target) is not int:
                unknown.append({
                    "field": f"talent_{slot}",
                    "character": identity,
                    "reason": "当前或目标天赋等级缺失",
                })
                continue
            if target < current or not 1 <= target <= 10:
                unknown.append({
                    "field": f"talent_{slot}",
                    "character": identity,
                    "reason": "天赋目标等级无效或低于当前等级",
                })
                continue
            for level in range(max(current + 1, 2), target + 1):
                for entry in talent_costs.get(f"lvl{level}", ()):
                    entries.append({
                        "resource_id": entry.get("id"),
                        "name": str(entry.get("name") or entry.get("id")),
                        "count": int(entry.get("count", 0)),
                    })
    else:
        unknown.append({
            "field": "talents",
            "character": identity,
            "reason": "未设置天赋目标等级",
        })

    target_constellation = character_goal.target_constellation
    if target_constellation is not None and target_constellation != character_state.constellation:
        unknown.append({
            "field": "constellation",
            "character": identity,
            "reason": "离线图鉴未提供通用命座养成摩拉与材料成本表",
        })

    build = character_goal.build
    target_weapon = build.weapon_id
    target_weapon_level = build.weapon_target_level
    if target_weapon is not None and target_weapon_level is not None:
        try:
            weapon_record = get_game_record("weapons", target_weapon, language="zh")
            weapon_costs = weapon_record.get("costs", {})
            target_stage = _target_ascension(target_weapon_level)
            same_weapon = target_weapon == character_state.weapon_id
            current_level = character_state.weapon_level if same_weapon else 1
            current_stage = _target_ascension(current_level) if same_weapon else 0
            entries.extend(_cost_entries(
                weapon_costs,
                "ascend",
                current_stage,
                target_stage,
            ))
        except (KeyError, TypeError, ValueError, RuntimeError):
            unknown.append({
                "field": "weapon",
                "character": identity,
                "reason": f"武器「{target_weapon}」离线突破费用快照不可用",
            })
    elif target_weapon is not None or target_weapon_level is not None:
        unknown.append({
            "field": "weapon",
            "character": identity,
            "reason": "武器名称或目标等级不完整",
        })

    if build.four_piece_set is not None:
        actions.append({
            "action_type": "artifact_farming",
            "target_four_piece_set": build.four_piece_set,
            "target_piece_count": 5,
            "resin": "unknown",
            "source": "existing_optimizer_not_yet_recomputed",
        })

    lines = _merge_costs(entries, inventory)
    return ResourceRequirement(lines=lines, unknown=tuple(unknown), actions=tuple(actions))


def aggregate_account_requirement(
    account_goal,
    account_snapshot,
    *,
    inventory: Mapping[str, object] | None = None,
) -> ResourceRequirement:
    states = {
        state.identity: state
        for state in account_snapshot.characters
    }
    lines = defaultdict(int)
    names = {}
    unknown = []
    actions = []
    for goal in account_goal.characters:
        state = states.get(goal.character_id)
        if state is None:
            unknown.append({
                "field": "character",
                "character": goal.character_id,
                "reason": "账号快照中不存在该目标角色",
            })
            continue
        requirement = aggregate_character_requirement(state, goal, inventory=inventory)
        unknown.extend(requirement.unknown)
        actions.extend(requirement.actions)
        for line in requirement.lines:
            key = str(line.resource_id)
            lines[key] += line.count
            names[key] = line.name
    available = _inventory_items(inventory)
    result_lines = []
    for resource_id, count in sorted(lines.items()):
        remaining = max(0, count - available.get(resource_id, 0))
        result_lines.append(ResourceLine(
            resource_id=202 if resource_id == "202" else resource_id,
            name="摩拉" if resource_id == "202" else names[resource_id],
            count=count,
            source="data/reference_data",
            remaining=remaining,
        ))
    return ResourceRequirement(
        lines=tuple(result_lines),
        unknown=tuple(unknown),
        actions=tuple(actions),
    )
