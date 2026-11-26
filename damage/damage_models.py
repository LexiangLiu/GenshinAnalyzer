"""Pluggable single-character damage models for loadout planning.

A model is deliberately a plain scalar function of a completed panel. The
optimizer and the resin estimator never contain character-specific constants;
new characters are added by registering a model here or by constructing a
``DamageModel`` from an objective expression.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Mapping

from .build_objective import BuildObjective
from data.game_data import character_profile


@dataclass(frozen=True)
class DamageModel:
    """A scalar damage model over panel stats and active set flags."""

    key: str
    character: str
    label: str
    expression: str
    scale: float = 1.0
    assumptions: Mapping[str, object] = field(default_factory=dict)
    panel_bonus_stats: Mapping[str, float] = field(default_factory=dict)
    target_four_piece_set: int | None = None
    monotone: bool = False
    increases_evaluator: object | None = field(default=None, repr=False, compare=False)
    _objective: BuildObjective = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.key or not self.character or not self.label:
            raise ValueError("伤害模型缺少标识、角色或名称")
        if type(self.scale) not in (int, float) or not isfinite(self.scale) or self.scale <= 0:
            raise ValueError("伤害模型倍率必须是有限正数")
        if type(self.monotone) is not bool:
            raise ValueError("monotone 必须是布尔值")
        object.__setattr__(self, "_objective", BuildObjective(self.expression))

    @property
    def objective(self) -> BuildObjective:
        return self._objective

    @property
    def relevant_stats(self) -> frozenset[str]:
        """Panel stats that a loadout can change and the expression reads."""
        names = {
            name for name in self._objective.names
            if not name.startswith("SET_") and not name.startswith("BASE_")
        }
        return frozenset(names | set(self._objective.substats))

    @property
    def set_flags(self) -> frozenset[str]:
        return frozenset(name for name in self._objective.names if name.startswith("SET_"))

    @property
    def substats(self) -> tuple[str, ...]:
        return self._objective.substats

    def evaluate(self, stats: Mapping[str, object], active_sets) -> float:
        return float(self._objective.evaluate(stats, active_sets)) * self.scale

    def evaluate_increases(self, stats: Mapping[str, object], active_sets, increases) -> float:
        """Evaluate a candidate substat increment, optionally with a fast path."""
        if self.increases_evaluator is not None:
            return float(self.increases_evaluator(stats, active_sets, increases))
        return float(self._objective.evaluate(stats, active_sets, increases)) * self.scale

    def scaled_expression(self) -> str:
        """Expression with the calibration multiplier, suitable for ``farming``."""
        return f"({self.expression}) * {self.scale!r}"

    def describe(self) -> dict:
        return {
            "key": self.key,
            "character": self.character,
            "label": self.label,
            "expression": self.expression,
            "scale": self.scale,
            "assumptions": dict(self.assumptions),
            "panel_bonus_stats": dict(self.panel_bonus_stats),
            "target_four_piece_set": self.target_four_piece_set,
            "monotone": self.monotone,
            "relevant_stats": sorted(self.relevant_stats),
        }


_TOME_NAMES = {
    "万世流涌大典", "tome of the eternal flow", "tomeoftheeternalflow", "14514",
}
_NEUVILLETTE_A1_MULTIPLIERS = (1.0, 1.1, 1.25, 1.6)
_NEUVILLETTE_SCALE = 0.072335


def neuvillette_charged_judgment(
    *,
    level: int = 90,
    ascension: int = 6,
    talents: Mapping[str, int] | None = None,
    constellation: int = 0,
    weapon: str | int = "万世流涌大典",
    weapon_level: int = 90,
    weapon_ascension: int = 6,
    refinement: int = 1,
    a1_stacks: int = 0,
    a4_hp: int = 50,
    weapon_ca_stacks: int = 3,
    four_piece_stacks: int = 3,
    crit_mode: str = "average",
) -> DamageModel:
    """The single-target, single-hit charged attack model for Neuvillette.

    The scenario is intentionally narrow: level 90, C0, talent 10/9/10,
    R1 Tome of the Eternal Flow at full stacks, A1 zero stacks, A4 at full HP,
    standard level-90 dummy defence and zero resistance. The calibration
    constant is taken from Genshin Optimizer's ``charged_judgment`` node.
    """
    if level != 90 or ascension != 6:
        raise ValueError("那维莱特模型当前只支持 90 级、突破 6")
    if constellation != 0:
        raise ValueError("那维莱特模型当前只支持 C0")
    talents = dict(talents or {"auto": 10, "skill": 9, "burst": 10})
    if talents.get("auto") != 10:
        raise ValueError("那维莱特模型当前只支持普攻天赋 10")
    if str(weapon).strip().casefold() not in _TOME_NAMES:
        raise ValueError("那维莱特模型当前只支持精一专武「万世流涌大典」")
    if weapon_level != 90 or weapon_ascension != 6 or refinement != 1:
        raise ValueError("那维莱特模型当前只支持 90 级、精一专武")
    for name, value, high in (
        ("a1_stacks", a1_stacks, 3),
        ("a4_hp", a4_hp, 50),
        ("weapon_ca_stacks", weapon_ca_stacks, 3),
        ("four_piece_stacks", four_piece_stacks, 3),
    ):
        if type(value) is not int or not 0 <= value <= high:
            raise ValueError(f"{name} 必须是 0 到 {high} 的整数")
    if crit_mode not in ("average", "noncrit"):
        raise ValueError("crit_mode 只能是 average 或 noncrit")

    a1_multiplier = _NEUVILLETTE_A1_MULTIPLIERS[a1_stacks]
    a4_bonus = min(30.0, 0.6 * a4_hp)
    weapon_bonus = 8.0 * weapon_ca_stacks
    four_piece_crit = 12.0 * four_piece_stacks
    crit = (
        f"(1 + min(100, CRIT_RATE + {four_piece_crit:g} * SET_15031_4) * CRIT_DMG / 10000)"
        if crit_mode == "average" else "1"
    )
    expression = (
        f"HP * (1 + ({a4_bonus:g} + 15 * SET_15031_2 + {weapon_bonus:g} + "
        f"HYDRO_DMG_BONUS) / 100) * {crit} * {a1_multiplier:g}"
    )

    def evaluate_increases(stats, active_sets, increases) -> float:
        hp_percent = float(stats.get("HP_PERCENT", 0)) + float(increases.get("HP_PERCENT", 0))
        hp = (
            float(stats.get("BASE_HP", 0)) * (1 + hp_percent / 100)
            + float(stats.get("HP", 0)) + float(increases.get("HP", 0))
        )
        dmg = (
            a4_bonus
            + (15.0 if "SET_15031_2" in active_sets else 0.0)
            + weapon_bonus
            + float(stats.get("HYDRO_DMG_BONUS", 0))
            + float(increases.get("HYDRO_DMG_BONUS", 0))
        )
        crit_rate = (
            float(stats.get("CRIT_RATE", 0)) + float(increases.get("CRIT_RATE", 0))
            + (four_piece_crit if "SET_15031_4" in active_sets else 0.0)
        )
        crit_dmg = float(stats.get("CRIT_DMG", 0)) + float(increases.get("CRIT_DMG", 0))
        crit_factor = 1.0 if crit_mode == "noncrit" else 1.0 + min(100.0, crit_rate) * crit_dmg / 10000
        return hp * (1 + dmg / 100) * crit_factor * a1_multiplier * _NEUVILLETTE_SCALE

    return DamageModel(
        key="neuvillette_charged_judgment",
        character="Neuvillette",
        label="那维莱特重击单段（C0/精一专武）",
        expression=expression,
        scale=_NEUVILLETTE_SCALE,
        assumptions={
            "character_level": level,
            "ascension": ascension,
            "talents": talents,
            "constellation": constellation,
            "weapon": "Tome of the Eternal Flow",
            "weapon_level": weapon_level,
            "weapon_ascension": weapon_ascension,
            "refinement": refinement,
            "a1_stacks": a1_stacks,
            "a4_hp": a4_hp,
            "a4_hydro_dmg_pct": a4_bonus,
            "weapon_ca_dmg_pct": weapon_bonus,
            "four_piece_stacks": four_piece_stacks,
            "four_piece_crit_rate_pct": four_piece_crit,
            "crit_mode": crit_mode,
            "target": "charged_judgment single hit",
            "enemy": {"level": 90, "resistance": 0, "defense": "standard same-level"},
            "calibration": "Genshin Optimizer 10.40.0 charged_judgment",
        },
        panel_bonus_stats={"HP_PERCENT": 16.0},
        target_four_piece_set=15031,
        monotone=True,
        increases_evaluator=evaluate_increases,
    )


_FARMING_PRIORITY = (
    "HP_PERCENT", "ATK_PERCENT", "DEF_PERCENT", "ENERGY_RECHARGE",
    "ELEMENTAL_MASTERY", "CRIT_RATE", "CRIT_DMG",
)
_FARMING_FLAT = ("HP", "ATK", "DEF")


def farming_substats(model: DamageModel, limit: int = 4) -> tuple[str, ...]:
    """Pick the highest-value substats for one-piece farming calculations.

    Exact enumeration grows quickly with the number of tracked substats.
    Plans use at most four percentage/offensive substats, which matches the
    hand-written Neuvillette path and keeps multi-character plans practical.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("limit 必须是正整数")
    relevant = set(model.substats)
    result = [stat for stat in _FARMING_PRIORITY if stat in relevant]
    result.extend(stat for stat in _FARMING_FLAT if stat in relevant)
    return tuple(result[:limit])


_FACTORIES = {
    "neuvillette_charged_judgment": neuvillette_charged_judgment,
}
_DEFAULT_MODELS = {
    "neuvillette": "neuvillette_charged_judgment",
}


def available_models() -> dict:
    result = {}
    for key, factory in _FACTORIES.items():
        model = factory()
        result[key] = {
            "character": model.character,
            "label": model.label,
            "target_four_piece_set": model.target_four_piece_set,
        }
    from .character_models import catalog_available
    result.update(catalog_available())
    return result


def get_damage_model(character: str | int, model: str | None = None, **options) -> DamageModel:
    """Resolve a hand-written or catalog model for one character."""
    canonical = character_profile(str(character))
    key = model or _DEFAULT_MODELS.get(canonical.casefold())
    if key is None:
        from .character_models import default_catalog_key
        key = default_catalog_key(canonical)
    if key is None:
        raise ValueError(f"暂无 {canonical} 的伤害模型；可通过 model 参数或注册表扩展")
    factory = _FACTORIES.get(key)
    if factory is not None:
        result = factory(**options)
    else:
        from .character_models import get_catalog_model
        result = get_catalog_model(key, **options)
    if result.character.casefold() != canonical.casefold():
        raise ValueError(f"伤害模型 {key} 属于 {result.character}，不能用于 {canonical}")
    return result
