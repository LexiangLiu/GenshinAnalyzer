from __future__ import annotations

from math import isfinite
from typing import Mapping, Sequence

from ..data.game_data import SUB_VALUES


_CRIT = {"CRIT_RATE": 2, "CRIT_DMG": 1}
_SCORES = {
    "atk": {"ATK": 0.13, "ATK_PERCENT": 1.345},
    "crit": _CRIT,
    "def": {"DEF": 0.11, "DEF_PERCENT": 1.07},
    "hp": {"HP": 0.0087, "HP_PERCENT": 1.345},
    "er": {"ENERGY_RECHARGE": 1.2},
    "em": {"ELEMENTAL_MASTERY": 0.339},
}
_SCORES["default"] = {**_SCORES["atk"], **_CRIT}


def score_weights(rules: str | Sequence[str]) -> dict[str, float]:
    presets = [rules] if isinstance(rules, str) else rules
    weights: dict[str, float] = {}
    for preset in presets:
        if preset not in _SCORES:
            raise ValueError(f"未知评分规则：{preset}")
        weights.update(_SCORES[preset])
    return weights


def score(substats: Mapping[str, float], weights: Mapping[str, float] | None = None) -> float:
    """Score active substats with caller-provided weights or default attack and crit weights."""
    weights = _SCORES["default"] if weights is None else weights
    if any(stat not in SUB_VALUES or not isfinite(weight) for stat, weight in weights.items()):
        raise ValueError("评分只接受有效的副词条和有限权重")
    return round(sum(value * weights.get(stat, 0) for stat, value in substats.items()), 6)
