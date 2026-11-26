"""Compose a combat panel from the local reference data and a caller's objective."""

from __future__ import annotations

import ast
import re
from decimal import Decimal, DecimalException

from ..data.game_data import MAIN_VALUES, SETS, SUB_VALUES
from ..data.references import get_game_record, get_game_stats


_CORE = ("HP", "ATK", "DEF")
_RAW_STATS = set(MAIN_VALUES) | set(SUB_VALUES) | {"SHIELD_STRENGTH"} | {
    f"{element}_RES" for element in ("PYRO", "HYDRO", "CRYO", "ELECTRO", "ANEMO", "GEO", "DENDRO")
}
_PANEL_NAMES = _RAW_STATS | {f"{prefix}_{stat}" for stat in _CORE for prefix in ("BASE", "FLAT")}
_SPECIALIZED = {
    "HP_PERCENT": "HP_PERCENT", "ATTACK_PERCENT": "ATK_PERCENT", "DEFENSE_PERCENT": "DEF_PERCENT",
    "CRITICAL": "CRIT_RATE", "CRITICAL_HURT": "CRIT_DMG", "CHARGE_EFFICIENCY": "ENERGY_RECHARGE",
    "ELEMENT_MASTERY": "ELEMENTAL_MASTERY", "HEAL_ADD": "HEALING_BONUS",
    **{f"{native}_ADD_HURT": f"{stat}_DMG_BONUS" for native, stat in zip(
        ("FIRE", "WATER", "ICE", "ELEC", "WIND", "ROCK", "GRASS", "PHYSICAL"),
        ("PYRO", "HYDRO", "CRYO", "ELECTRO", "ANEMO", "GEO", "DENDRO", "PHYSICAL"),
    )},
}


def as_decimal(value) -> Decimal:
    if type(value) not in (int, float, Decimal):
        raise ValueError("必须提供有限数值")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("必须提供有限数值")
    return number


def character_weapon_panel(character: dict | None, weapon: dict | None, bonus_stats=None):
    stats = {key: Decimal(0) for key in _RAW_STATS | {f"BASE_{key}" for key in _CORE}}
    stats.update(CRIT_RATE=Decimal(5), CRIT_DMG=Decimal(50), ENERGY_RECHARGE=Decimal(100))
    result = {}
    if weapon is not None and character is None:
        raise ValueError("填写武器时也需要填写角色")
    if bonus_stats is not None and not isinstance(bonus_stats, dict):
        raise ValueError("bonus_stats 必须是属性名到数值的对象")
    weapon_types = {}
    for request, category, label in ((character, "characters", "character"), (weapon, "weapons", "weapon")):
        if request is None:
            continue
        if not isinstance(request, dict) or set(request) - {"id", "level", "ascension"}:
            raise ValueError(f"{label} 支持 id、level 和 ascension")
        row = get_game_stats(category, request["id"], request["level"], ascension=request.get("ascension", "before"))
        record = get_game_record(category, row["id"])
        weapon_types[label] = record["weaponType"]
        result[label] = row
        for key, field in zip(_CORE, ("hp", "attack", "defense")):
            stats[f"BASE_{key}"] += as_decimal(row.get(field, 0))
        prop = row.get("specialized_type")
        if prop is not None and "specialized" in row:
            stat = _SPECIALIZED[prop.removeprefix("FIGHT_PROP_")]
            amount = as_decimal(row["specialized"]) * (1 if stat == "ELEMENTAL_MASTERY" else 100)
            if label == "character" and stat in {"CRIT_RATE", "CRIT_DMG"}:
                stats[stat] = amount
            else:
                stats[stat] += amount
    if weapon is not None and weapon_types["character"] != weapon_types["weapon"]:
        raise ValueError("该角色无法装备此武器类型")
    for name, value in (bonus_stats or {}).items():
        if name not in _RAW_STATS:
            raise ValueError(f"未知的额外属性 {name}")
        stats[name] += as_decimal(value)
    return stats, result


class BuildObjective:
    """A scalar formula over panel stats and equipped-set activation flags."""

    def __init__(self, expression: str) -> None:
        if not isinstance(expression, str) or len(expression) > 1024:
            raise ValueError("objective 必须是长度不超过 1024 的公式字符串")
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"无效的目标公式：{exc.msg}") from exc
        self.expression = expression
        self.names: set[str] = set()
        self.constants: dict[str, Decimal] = {}
        tree.body = self._operand(tree.body)
        self._code = compile(ast.fix_missing_locations(tree), "<farming objective>", "eval")
        needed = set(self.names)
        for stat in _CORE:
            if stat in self.names:
                needed.add(f"{stat}_PERCENT")
            if f"FLAT_{stat}" in self.names:
                needed.add(stat)
        self.substats = tuple(stat for stat in SUB_VALUES if stat in needed)

    def _validate(self, node) -> None:
        if isinstance(node, ast.Name):
            match = re.fullmatch(r"SET_([1-9]\d*)_([24])", node.id)
            if node.id not in _PANEL_NAMES and not (match and match[2] in SETS.get(int(match[1]), {}).get("effects", {})):
                raise ValueError(f"未知的配装公式变量：{node.id}")
            self.names.add(node.id)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            for attr in ("left", "right"):
                setattr(node, attr, self._operand(getattr(node, attr)))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            node.operand = self._operand(node.operand)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in ("min", "max") and len(node.args) >= 2 and not node.keywords):
            node.args = [self._operand(arg) for arg in node.args]
        else:
            raise ValueError("公式支持属性、数值、+ − * /、min 和 max")

    def _operand(self, node):
        if isinstance(node, ast.Constant):
            key = f"_constant_{len(self.constants)}"
            self.constants[key] = as_decimal(node.value)
            return ast.copy_location(ast.Name(id=key, ctx=ast.Load()), node)
        self._validate(node)
        return node

    def evaluate(self, stats, active_sets, increases=None) -> Decimal:
        variables = self.constants.copy()
        increases = {} if increases is None else increases
        for name in self.names:
            if name in _CORE:
                base = stats[f"BASE_{name}"]
                percent = stats[f"{name}_PERCENT"] + increases.get(f"{name}_PERCENT", 0)
                variables[name] = base + base * percent / 100 + stats[name] + increases.get(name, 0)
            elif name.startswith("FLAT_"):
                stat = name.removeprefix("FLAT_")
                variables[name] = stats[stat] + increases.get(stat, 0)
            elif name.startswith("SET_"):
                variables[name] = Decimal(name in active_sets)
            else:
                variables[name] = stats[name] + increases.get(name, 0)
        try:
            result = eval(self._code, {"__builtins__": {}, "min": min, "max": max}, variables)
        except DecimalException as exc:
            raise ValueError("目标公式在某个配装属性下无法计算") from exc
        if not result.is_finite():
            raise ValueError("目标公式必须输出有限数值")
        return result
