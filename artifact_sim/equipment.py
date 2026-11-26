from __future__ import annotations

import re
from collections import Counter, defaultdict
from decimal import Decimal
from math import isfinite
from typing import Sequence

from .game_data import SETS
from .models import Artifact


_PANEL_BONUSES = (
    (r"攻击力提高([0-9]+)%", "ATK_PERCENT"),
    (r"防御力提高([0-9]+)%", "DEF_PERCENT"),
    (r"生命值提升([0-9]+)%", "HP_PERCENT"),
    (r"元素充能效率提高([0-9]+)%", "ENERGY_RECHARGE"),
    (r"元素精通提高([0-9]+)点", "ELEMENTAL_MASTERY"),
    (r"(?:治疗加成提高|角色造成的治疗效果提升)([0-9]+)%", "HEALING_BONUS"),
    (r"护盾强效提高([0-9]+)%", "SHIELD_STRENGTH"),
    (r"造成的物理伤害提高([0-9]+)%", "PHYSICAL_DMG_BONUS"),
) + tuple(
    rule
    for zh, en in zip("火雷水风岩草冰", ("PYRO", "ELECTRO", "HYDRO", "ANEMO", "GEO", "DENDRO", "CRYO"))
    for rule in (
        (rf"获得([0-9]+)%{zh}元素伤害加成", f"{en}_DMG_BONUS"),
        (rf"{zh}元素抗性提高([0-9]+)%", f"{en}_RES"),
    )
)


def mastery_bonus(mastery: float) -> dict[str, float]:
    if not isfinite(mastery) or mastery < 0:
        raise ValueError("元素精通必须是有限的非负数")
    return {
        "amplifying_percent": round(278 * mastery / (mastery + 1400), 1),
        "transformative_percent": round(1600 * mastery / (mastery + 2000), 1),
        "crystallize_shield_percent": round(444 * mastery / (mastery + 1400), 1),
    }


def set_effects(counts: dict[int, int], language: str = "zh") -> tuple[dict[str, Decimal], list[dict]]:
    bonuses = []
    static: dict[str, Decimal] = defaultdict(Decimal)
    suffix = "_en" if language == "en" else ""
    for set_id, count in counts.items():
        info = SETS[set_id]
        for threshold, effect in info["effects"].items():
            if count < int(threshold):
                continue
            bonuses.append({
                "set_id": set_id, "name": info["name" + suffix], "pieces": int(threshold),
                "effect": info["effects" + suffix].get(threshold, effect),
            })
            for pattern, stat in _PANEL_BONUSES:
                match = re.fullmatch(pattern + r"。?", effect)
                if match:
                    static[stat] += Decimal(match[1])
                    break
    return static, bonuses


def loadout(artifacts: Sequence[Artifact], language: str = "zh") -> dict:
    """Aggregate the artifact contribution and unlocked set-bonus descriptions."""
    if len({piece.slot for piece in artifacts}) != len(artifacts):
        raise ValueError("同一配装不能装备两个同部位圣遗物")
    stats: dict[str, Decimal] = defaultdict(Decimal)
    counts = Counter(piece.set_id for piece in artifacts)
    for piece in artifacts:
        stats[piece.main_stat] += Decimal(str(piece.main_value))
        for stat, value in piece.substats().items():
            stats[stat] += Decimal(str(value))
    static, bonuses = set_effects(counts, language)
    panel = {stat: float(stats.get(stat, 0) + static.get(stat, 0)) for stat in stats.keys() | static.keys()}
    return {
        "artifact_stats": {stat: float(value) for stat, value in stats.items()},
        "static_set_stats": {stat: float(value) for stat, value in static.items()},
        "panel_stats": dict(sorted(panel.items())),
        "mastery_bonus": mastery_bonus(panel.get("ELEMENTAL_MASTERY", 0)),
        "set_counts": dict(counts),
        "unlocked_set_bonuses": bonuses,
    }
