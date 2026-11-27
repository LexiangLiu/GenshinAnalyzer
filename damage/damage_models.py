"""Pluggable single-character damage models for loadout planning.

A model is deliberately a plain scalar function of a completed panel. The
optimizer and the resin estimator never contain character-specific constants;
new characters are added by registering a model here or by constructing a
``DamageModel`` from an objective expression.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from math import isfinite
from typing import Mapping

from .build_objective import BuildObjective
from data.game_data import character_profile


@lru_cache(maxsize=10)
def _neuvillette_talent_multiplier(level: int) -> float:
    from data.references import get_game_record

    record = get_game_record("talents", "Neuvillette", language="en")
    values = record["combat1"]["attributes"]["parameters"]["param5"]
    if type(level) is not int or not 1 <= level <= 10 or len(values) < 10:
        raise ValueError("那维莱特普攻天赋等级必须是 1 到 10")
    return float(values[level - 1]) / float(values[9])


def _number(value: float) -> str:
    return f"{float(value):.12g}"


def _sum_expression(terms: list[str]) -> str:
    return " + ".join(terms) if terms else "0"


def _formula_term_expression(term: Mapping[str, object]) -> str:
    kind = term.get("kind")
    if kind == "stat":
        coefficient = float(term.get("coefficient", 1.0))
        stat = str(term["stat"])
        return stat if coefficient == 1 else f"{_number(coefficient)} * {stat}"
    if kind == "constant":
        return _number(float(term["value"]))
    if kind == "constant_percent":
        return _number(float(term["value"]))
    flag = f"SET_{int(term['set_id'])}_{int(term['pieces'])}"
    if kind == "set_stat_percent":
        return f"{_number(float(term['coefficient']))} * {term['base_stat']} * {flag}"
    if kind == "set_stat_flat":
        return f"{_number(float(term['coefficient']))} * {flag}"
    if kind == "set_percent":
        return f"{_number(float(term['value']))} * {flag}"
    if kind == "set_er_percent":
        return f"min({_number(float(term['cap']))}, {_number(float(term['value']))} * ENERGY_RECHARGE) * {flag}"
    raise ValueError(f"不支持的公式因子类型：{kind}")


def formula_expression(factors: Mapping[str, object]) -> str:
    """Render the scoring expression from the same factors used by evaluation."""
    scaling = _sum_expression([
        _formula_term_expression(term) for term in factors.get("scaling_terms", [])
    ])
    damage_bonus = _sum_expression([
        _formula_term_expression(term) for term in factors.get("damage_terms", [])
    ])
    parts = [f"({scaling})", f"(1 + ({damage_bonus}) / 100)"]
    if factors.get("crit_enabled", True):
        crit_rate = _sum_expression([
            _formula_term_expression(term) for term in factors.get("crit_rate_terms", [])
        ])
        crit_dmg = _sum_expression([
            _formula_term_expression(term) for term in factors.get("crit_dmg_terms", [])
        ])
        parts.append(f"(1 + min(100, {crit_rate}) * ({crit_dmg}) / 10000)")
    for multiplier in factors.get("fixed_multipliers", []):
        parts.append(_number(float(multiplier["value"])))
    return " * ".join(parts)


def evaluate_formula_breakdown(
    factors: Mapping[str, object],
    stats: Mapping[str, object],
    active_sets,
    increases: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Return term-level inputs and the exact product used by formula scoring."""
    extra = increases or {}

    def stat_value(name: str) -> float:
        if name in {"HP", "ATK", "DEF"}:
            base = float(stats.get(f"BASE_{name}", 0))
            percent = float(stats.get(f"{name}_PERCENT", 0)) + float(extra.get(f"{name}_PERCENT", 0))
            return base + base * percent / 100 + float(stats.get(name, 0)) + float(extra.get(name, 0))
        return float(stats.get(name, 0)) + float(extra.get(name, 0))

    def term_value(term: Mapping[str, object]) -> float:
        kind = term.get("kind")
        if kind == "stat":
            return float(term.get("coefficient", 1.0)) * stat_value(str(term["stat"]))
        if kind == "constant":
            return float(term["value"])
        if kind == "constant_percent":
            return float(term["value"])
        flag = f"SET_{int(term['set_id'])}_{int(term['pieces'])}"
        if flag not in active_sets:
            return 0.0
        if kind == "set_stat_percent":
            return float(term["coefficient"]) * stat_value(str(term["base_stat"]))
        if kind == "set_stat_flat":
            return float(term["coefficient"])
        if kind == "set_percent":
            return float(term["value"])
        if kind == "set_er_percent":
            return min(float(term["cap"]), float(term["value"]) * stat_value("ENERGY_RECHARGE"))
        raise ValueError(f"不支持的公式因子类型：{kind}")

    scaling_values = [term_value(term) for term in factors.get("scaling_terms", [])]
    damage_values = [term_value(term) for term in factors.get("damage_terms", [])]
    scaling = sum(scaling_values)
    damage_bonus = sum(damage_values)
    damage_multiplier = 1 + damage_bonus / 100
    result = scaling * damage_multiplier
    crit_rate_values = []
    crit_dmg_values = []
    crit_rate = None
    crit_dmg = None
    crit_multiplier = None
    if factors.get("crit_enabled", True):
        crit_rate_values = [
            term_value(term) for term in factors.get("crit_rate_terms", [])
        ]
        crit_dmg_values = [
            term_value(term) for term in factors.get("crit_dmg_terms", [])
        ]
        crit_rate = sum(crit_rate_values)
        crit_dmg = sum(crit_dmg_values)
        crit_multiplier = 1 + min(100, crit_rate) * crit_dmg / 10000
        result *= crit_multiplier
    fixed_multiplier_values = [
        float(multiplier["value"]) for multiplier in factors.get("fixed_multipliers", [])
    ]
    for value in fixed_multiplier_values:
        result *= value
    return {
        "scaling_term_values": scaling_values,
        "damage_term_values": damage_values,
        "crit_rate_term_values": crit_rate_values,
        "crit_dmg_term_values": crit_dmg_values,
        "fixed_multiplier_values": fixed_multiplier_values,
        "scaling": scaling,
        "damage_bonus": damage_bonus,
        "damage_multiplier": damage_multiplier,
        "crit_rate": crit_rate,
        "crit_dmg": crit_dmg,
        "crit_multiplier": crit_multiplier,
        "unscaled_score": result,
    }


def evaluate_formula_factors(
    factors: Mapping[str, object],
    stats: Mapping[str, object],
    active_sets,
    increases: Mapping[str, object] | None = None,
) -> float:
    """Evaluate the structured scoring factors shared with formula display."""
    return float(evaluate_formula_breakdown(factors, stats, active_sets, increases)["unscaled_score"])


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
    formula_factors: Mapping[str, object] = field(default_factory=dict)
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
        if self.formula_factors:
            return evaluate_formula_factors(self.formula_factors, stats, active_sets) * self.scale
        return float(self._objective.evaluate(stats, active_sets)) * self.scale

    def evaluate_increases(self, stats: Mapping[str, object], active_sets, increases) -> float:
        """Evaluate a candidate substat increment, optionally with a fast path."""
        if self.increases_evaluator is not None:
            return float(self.increases_evaluator(stats, active_sets, increases))
        if self.formula_factors:
            return evaluate_formula_factors(self.formula_factors, stats, active_sets, increases) * self.scale
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
            "formula_factors": dict(self.formula_factors),
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
    for talent, value in talents.items():
        if type(value) is not int or not 1 <= value <= 10:
            raise ValueError(f"talents.{talent} 必须是 1 到 10 的整数")
    talent_level = talents.get("auto", 10)
    talent_multiplier = _neuvillette_talent_multiplier(talent_level)
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
    factors = {
        "scaling_terms": [{"kind": "stat", "stat": "HP", "coefficient": 1.0}],
        "damage_terms": [
            {
                "kind": "constant_percent",
                "value": a4_bonus,
                "source_label": "角色固有天赋伤害加成",
            },
            {"kind": "set_percent", "value": 15.0, "set_id": 15031, "pieces": 2},
            {
                "kind": "constant_percent",
                "value": weapon_bonus,
                "source_label": "万世流涌大典被动伤害加成",
            },
            {"kind": "stat", "stat": "HYDRO_DMG_BONUS"},
        ],
        "crit_rate_terms": [
            {"kind": "stat", "stat": "CRIT_RATE"},
            {"kind": "set_percent", "value": four_piece_crit, "set_id": 15031, "pieces": 4},
        ],
        "crit_dmg_terms": [{"kind": "stat", "stat": "CRIT_DMG"}],
        "crit_enabled": crit_mode == "average",
        "fixed_multipliers": [{"label": "那维莱特固有天赋", "value": a1_multiplier}],
        "talent": {
            "slot": "auto",
            "level": talent_level,
            "baseline_level": 10,
            "multiplier": talent_multiplier,
            "source": "本地角色天赋数据 combat1.param5；以 Lv.10 为基准",
        },
        "external_inputs": {
            "panel": "角色与武器面板、圣遗物主副词条和已解析的常驻套装属性",
            "weapon_passive": f"万世流涌大典重击伤害加成 {weapon_bonus:g}%",
            "set_effects": [
                {"set_id": 15031, "pieces": 2, "values": {"dmg": 15.0}},
                {"set_id": 15031, "pieces": 4, "values": {"crit_rate": four_piece_crit}},
            ],
            "set_effects_note": "只计入逐影猎人已建模效果；其他套装效果不计入评分。",
        },
    }
    expression = formula_expression(factors)

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
        return (
            hp * (1 + dmg / 100) * crit_factor * a1_multiplier
            * _NEUVILLETTE_SCALE * talent_multiplier
        )

    return DamageModel(
        key="neuvillette_charged_judgment",
        character="Neuvillette",
        label="那维莱特重击单段（C0/精一专武）",
        expression=expression,
        scale=_NEUVILLETTE_SCALE * talent_multiplier,
        assumptions={
            "character_level": level,
            "ascension": ascension,
            "talents": talents,
            "talent_multiplier": talent_multiplier,
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
        formula_factors=factors,
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
    from data.references import is_manekin_identity, is_traveler_identity, traveler_form
    if is_manekin_identity(character):
        raise ValueError("奇偶的技能倍率尚未校准，不参与伤害模型和配装评分")
    if is_traveler_identity(character):
        form = traveler_form(character)
        if form is None:
            raise ValueError("旅行者必须指定当前元素形态，空与荧不能作为不同配装角色")
        canonical = str(form["name_en"])
    else:
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
