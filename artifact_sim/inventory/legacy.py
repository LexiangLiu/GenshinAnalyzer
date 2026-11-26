"""Read the JSON arrays exported by the retired web simulator."""

from __future__ import annotations

import json
from hashlib import sha256
from math import isclose, isfinite

from ..core.models import Artifact, Enhancement, Roll
from ..core.simulation import Simulator
from ..data.game_data import character_profile


_STATS = {
    "HP": "HP", "ATK": "ATK", "DEF": "DEF",
    "HPPer": "HP_PERCENT", "ATKPer": "ATK_PERCENT", "DEFPer": "DEF_PERCENT",
    "CRITRate": "CRIT_RATE", "CRITDMG": "CRIT_DMG", "energyRecharge": "ENERGY_RECHARGE",
    "elementMastery": "ELEMENTAL_MASTERY", "Healing": "HEALING_BONUS",
    **{element: f"{element.upper()}_DMG_BONUS" for element in (
        "Pyro", "Electro", "Hydro", "Anemo", "Geo", "Dendro", "Cryo", "Physical",
    )},
}


def _tier(stat: str, value: float) -> int:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError("旧版副词条数值无效")
    variants = [Roll(stat, tier) for tier in range(1, 5)]
    nearest = min(variants, key=lambda roll: abs(roll.value - value))
    if abs(nearest.value - value) > 0.21:
        raise ValueError(f"旧版词条数值超出合法档位：{stat} {value}")
    return nearest.tier


def _convert(item: dict) -> Artifact:
    old_set = item["set"]
    set_name = "Blizzard Strayer" if old_set == "Bizzard Strayer" else old_set
    start = json.loads(item["initEntry"])
    history = item["upgradeHistory"]
    level = item["level"]
    if len(start) not in (3, 4) or type(level) is not int or not 0 <= level <= 20 or level % 4:
        raise ValueError("旧版初始副词条或等级无效")
    if len(history) != level // 4:
        raise ValueError("旧版强化记录与等级不匹配")
    initial = [(_STATS[stat], _tier(_STATS[stat], value)) for stat, value in start]
    old_values = dict(start)
    events = []
    for at_level, (old_stat, value) in zip(range(4, level + 1, 4), history):
        stat = _STATS[old_stat]
        tier = _tier(stat, value)
        if at_level == 4 and len(start) == 3:
            initial.append((stat, tier))
        else:
            index = [name for name, _ in initial].index(stat)
            events.append(Enhancement(at_level, index, tier))
        old_values[old_stat] = old_values.get(old_stat, 0) + value
    totals = dict(item["entry"])
    if set(totals) != set(old_values) or any(not isclose(totals[stat], value, abs_tol=0.001) for stat, value in old_values.items()):
        raise ValueError("旧版词条总值与强化记录不匹配")
    # The old model never drew a fourth line on unenhanced three-line pieces.
    seed = int.from_bytes(sha256(item["symbol"].encode()).digest(), "big")
    base = Simulator(seed).create(
        set_name, slot=item["part"].upper(), main_stat=_STATS[item["mainEntry"]],
        initial_count=len(start), substats=initial,
    )
    return Artifact(base.set_id, base.slot, base.main_stat, base.initial_count, base.initial_rolls,
                    level, tuple(events))


def legacy_backup(items: list) -> dict:
    if not isinstance(items, list):
        raise ValueError("旧版导出应为 JSON 数组")
    profiles = set()
    artifacts = []
    for index, item in enumerate(items, 1):
        try:
            artifact = _convert(item)
            wearer = character_profile(item["equipped"]) if item["equipped"] else None
            if wearer is not None:
                profiles.add(wearer)
            artifacts.append({
                "id": item["symbol"], "state": artifact.to_state(), "locked": item["lock"],
                "is_new": item["isNew"], "equipped": wearer, "deleted_order": None,
            })
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"旧版存档第 {index} 件无法导入：{exc}") from exc
    return {
        "inventory_schema": 1, "artifacts": artifacts, "profiles": sorted(profiles),
        "preferences": {},
    }
