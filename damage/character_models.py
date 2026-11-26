"""Data-driven character damage models calibrated from Genshin Optimizer.

The hand-written models in :mod:`artifact_sim.damage_models` pin a narrow
scenario.  This module complements them with a catalog of single-hit models
generated from the local Genshin Optimizer bundle: talent scaling, ascension
passives and weapon-independent kit buffs are probed at level 90 / C0 /
talents 10-10-10, then reduced to a scalar :class:`DamageModel` expression over
the final panel.

The catalog is a plain JSON data file so the optimizer can evaluate hundreds
of candidate loadouts without starting Node.  Rebuild it with
``tools/calibrate_character_models.py``.

Scope of a catalog model:

* one named damage instance of one character;
* no elemental reactions, team buffs or enemy debuffs;
* the caller supplies the weapon's base stats and substat through
  :func:`artifact_sim.build_objective.character_weapon_panel`;
* weapon passives are not part of the generated catalog.  The hand-written
  Neuvillette model remains the reference for a weapon-specific scenario.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from .damage_models import DamageModel


# Only conditional, damage-relevant set effects live here.  Unconditional ATK%,
# HP%, DEF%, EM, ER and elemental DMG bonuses are already parsed by
# ``equipment.set_effects`` and must not be duplicated.
SET_MODEL_EFFECTS: dict[int, dict[str, dict]] = {
    14001: {  # Blizzard Strayer
        "4": {"crit_rate": 40},
    },
    15001: {  # Gladiator's Finale
        "4": {"dmg": 35, "when": {"normal"}},
    },
    15002: {},  # Viridescent Venerer: RES shred, not personal damage
    15003: {  # Wanderer's Troupe
        "4": {"dmg": 35, "when": {"charged"}},
    },
    15005: {},  # Thundering Fury: reaction damage
    15006: {},  # Crimson Witch: reaction damage / 2pc stacks
    15007: {  # Noblesse Oblige
        "2": {"dmg": 20, "when": {"burst"}},
        "4": {"atk_percent": 20},
    },
    15008: {  # Bloodstained Chivalry
        "4": {"dmg": 50, "when": {"charged"}},
    },
    15015: {  # Retracing Bolide
        "4": {"dmg": 40, "when": {"normal", "charged"}},
    },
    15016: {  # Heart of Depth
        "4": {"dmg": 30, "when": {"normal", "charged"}},
    },
    15017: {  # Tenacity of the Millelith
        "4": {"atk_percent": 20},
    },
    15018: {  # Pale Flame
        "4": {"atk_percent": 18},
    },
    15019: {  # Shimenawa's Reminiscence
        "4": {"dmg": 50, "when": {"normal", "charged", "plunge"}},
    },
    15020: {  # Emblem of Severed Fate
        "4": {"er_burst_dmg": 0.25, "when": {"burst"}},
    },
    15021: {  # Husk of Opulent Dreams
        "4": {"def_percent": 24, "dmg": 24, "element_only": "GEO"},
    },
    15022: {},  # Ocean-Hued Clam: healing
    15023: {  # Vermillion Hereafter
        "4": {"atk_percent": 48},
    },
    15024: {},  # Echoes of an Offering: probabilistic ATK-based normal damage
    15025: {},  # Deepwood Memories: RES shred
    15026: {  # Gilded Dreams (three different elements)
        "4": {"em": 150},
    },
    15027: {  # Desert Pavilion Chronicle
        "4": {"dmg": 40, "when": {"normal", "charged", "plunge"}},
    },
    15028: {},  # Flower of Paradise Lost: bloom reactions
    15029: {  # Nymph's Dream (three stacks)
        "4": {"atk_percent": 25, "dmg": 15},
    },
    15030: {},  # Vourukasha's Glow: skill/burst conditional, handled by HP-scaling models
    15031: {  # Marechaussee Hunter
        "2": {"dmg": 15, "when": {"normal", "charged"}},
        "4": {"crit_rate": 36},
    },
    15032: {  # Golden Troupe (on-field appraisal)
        "2": {"dmg": 20, "when": {"skill"}},
        "4": {"dmg": 25, "when": {"skill"}},
    },
    15033: {},  # Song of Days Past: healing
    15034: {  # Nighttime Whispers in the Echoing Woods (shielded)
        "4": {"dmg": 50, "element_only": "GEO"},
    },
    15035: {  # Fragment of Harmonic Whimsy
        "4": {"dmg": 54},
    },
    15036: {  # Unfinished Reverie
        "4": {"dmg": 50},
    },
    15037: {  # Scroll of the Hero of Cinder City (personal share)
        "4": {"dmg": 12},
    },
    15038: {  # Obsidian Codex
        "2": {"dmg": 15},
        "4": {"crit_rate": 40},
    },
    15039: {  # Long Night's Oath
        "2": {"dmg": 25, "when": {"plunge"}},
        "4": {"dmg": 75, "when": {"plunge"}},
    },
    15040: {  # Finale of the Deep Galleries
        "4": {"dmg": 60, "when": {"normal", "burst"}},
    },
    15041: {  # Night of the Sky's Unveiling (Nascent Gleam)
        "4": {"crit_rate": 15},
    },
    15042: {  # Silken Moon's Serenade (Nascent Gleam)
        "4": {"em": 60},
    },
    15043: {},  # Aubade of Morningstar and Moon: lunar reactions
    15044: {  # A Day Carved From Rising Winds
        "4": {"atk_percent": 25, "crit_rate": 20},
    },
    15045: {  # Celestial Gift (single element)
        "4": {"dmg": 20},
    },
    15046: {  # Disenchantment in Deep Shadow
        "4": {"crit_rate": 16},
    },
    15047: {  # Scarlet Proof
        "4": {"crit_rate": 16},
    },
    15048: {  # Heart of the Furnace
        "4": {"atk_percent": 12},
    },
}

EFFECT_KEYS = frozenset({
    "dmg", "crit_rate", "crit_dmg", "atk_percent", "hp_percent", "def_percent",
    "em", "flat_atk", "er_burst_dmg", "when", "element_only",
})


def resolve_set_effects(
    set_id: int | None,
    attack_category: str | None,
    element: str | None = None,
) -> dict[str, dict]:
    """Return the modeled conditional effects of one four-piece set.

    ``attack_category`` is one of normal/charged/plunge/skill/burst.  Effects
    tied to a different category or element are omitted.
    """
    if set_id is None:
        return {}
    result: dict[str, dict] = {}
    for pieces, effect in SET_MODEL_EFFECTS.get(set_id, {}).items():
        when = effect.get("when")
        element_only = effect.get("element_only")
        if when and attack_category not in when:
            continue
        if element_only and element != element_only:
            continue
        cleaned = {key: float(value) for key, value in effect.items() if key in EFFECT_KEYS and type(value) in (int, float)}
        if cleaned:
            result[pieces] = cleaned
    return result


def _number(value: float) -> str:
    return f"{float(value):.12g}"


def _join(parts: list[str]) -> str:
    return " + ".join(parts) if parts else "0"


def build_expression(spec: Mapping[str, object], crit_mode: str = "average") -> str:
    """Compose the scalar objective expression for one catalog model."""
    coefficients = dict(spec.get("coefficients") or {})
    element = str(spec.get("element") or "PHYSICAL").upper()
    element_stat = "PHYSICAL_DMG_BONUS" if element in ("PHYSICAL", "NONE") else f"{element}_DMG_BONUS"

    scaling_terms = [
        f"{_number(float(coefficients[stat]))} * {stat}"
        for stat in ("HP", "ATK", "DEF", "ELEMENTAL_MASTERY", "ENERGY_RECHARGE")
        if abs(float(coefficients.get(stat, 0.0))) > 1e-12
    ]
    constant = float(spec.get("constant", 0.0))
    if abs(constant) > 1e-12:
        scaling_terms.append(_number(constant))

    effects = spec.get("set_effects") or {}
    damage_terms = [element_stat]
    crit_rate_terms = ["CRIT_RATE"]
    crit_dmg_terms = ["CRIT_DMG"]
    for set_id, levels in effects.items():
        for pieces, effect in levels.items():
            flag = f"SET_{set_id}_{pieces}"
            if "atk_percent" in effect and coefficients.get("ATK"):
                scaling_terms.append(
                    f"{_number(float(coefficients['ATK']) * float(effect['atk_percent']) / 100)} * BASE_ATK * {flag}",
                )
            if "hp_percent" in effect and coefficients.get("HP"):
                scaling_terms.append(
                    f"{_number(float(coefficients['HP']) * float(effect['hp_percent']) / 100)} * BASE_HP * {flag}",
                )
            if "def_percent" in effect and coefficients.get("DEF"):
                scaling_terms.append(
                    f"{_number(float(coefficients['DEF']) * float(effect['def_percent']) / 100)} * BASE_DEF * {flag}",
                )
            if "em" in effect and coefficients.get("ELEMENTAL_MASTERY"):
                scaling_terms.append(
                    f"{_number(float(coefficients['ELEMENTAL_MASTERY']) * float(effect['em']))} * {flag}",
                )
            if effect.get("dmg"):
                damage_terms.append(f"{_number(float(effect['dmg']))} * {flag}")
            if effect.get("er_burst_dmg"):
                damage_terms.append(f"min(75, {_number(float(effect['er_burst_dmg']))} * ENERGY_RECHARGE) * {flag}")
            if effect.get("crit_rate"):
                crit_rate_terms.append(f"{_number(float(effect['crit_rate']))} * {flag}")
            if effect.get("crit_dmg"):
                crit_dmg_terms.append(f"{_number(float(effect['crit_dmg']))} * {flag}")

    if spec.get("dmg_bonus"):
        damage_terms.append(_number(float(spec["dmg_bonus"])))
    if spec.get("crit_rate_bonus"):
        crit_rate_terms.append(_number(float(spec["crit_rate_bonus"])))
    if spec.get("crit_dmg_bonus"):
        crit_dmg_terms.append(_number(float(spec["crit_dmg_bonus"])))

    scaling = _join(scaling_terms)
    damage = f"(1 + ({_join(damage_terms)}) / 100)"
    parts = [f"({scaling})", damage]
    if crit_mode == "average" and spec.get("crit_enabled", True):
        crit = f"(1 + min(100, {_join(crit_rate_terms)}) * ({_join(crit_dmg_terms)}) / 10000)"
        parts.append(crit)
    return " * ".join(parts)


def _spec_monotone(spec: Mapping[str, object]) -> bool:
    if float(spec.get("constant", 0.0)) < 0:
        return False
    for value in (spec.get("coefficients") or {}).values():
        if float(value) < 0:
            return False
    if float(spec.get("dmg_bonus", 0.0)) < -100:
        return False
    return True


def model_from_spec(spec: Mapping[str, object], crit_mode: str = "average") -> DamageModel:
    if crit_mode not in ("average", "noncrit"):
        raise ValueError("crit_mode 只能是 average 或 noncrit")
    expression = build_expression(spec, crit_mode)
    assumptions = dict(spec.get("assumptions") or {})
    assumptions["crit_mode"] = crit_mode
    assumptions.setdefault("attack", spec.get("attack"))
    assumptions.setdefault("category", spec.get("category"))
    assumptions.setdefault("element", spec.get("element"))
    assumptions.setdefault("calibration", spec.get("calibration"))
    if spec.get("sources"):
        assumptions["sources"] = list(spec["sources"])
    return DamageModel(
        key=str(spec["key"]),
        character=str(spec["character"]),
        label=str(spec["label"]),
        expression=expression,
        scale=1.0,
        assumptions=assumptions,
        panel_bonus_stats=dict(spec.get("panel_bonus_stats") or {}),
        target_four_piece_set=int(spec["target_four_piece_set"]) if spec.get("target_four_piece_set") is not None else None,
        monotone=_spec_monotone(spec),
    )


@lru_cache(maxsize=1)
def _catalog() -> dict:
    path = Path(__file__).with_name("character_models.json")
    if not path.is_file():
        return {"schema_version": 1, "models": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("不支持的角色模型目录版本")
    return data


def catalog_models() -> list[dict]:
    return list(_catalog().get("models", []))


def catalog_metadata() -> dict:
    data = _catalog()
    return {
        "schema_version": data.get("schema_version", 1),
        "generated_at": data.get("generated_at"),
        "upstream": data.get("upstream"),
        "count": len(data.get("models", [])),
    }


def catalog_spec(key: str) -> dict:
    for spec in catalog_models():
        if spec.get("key") == key:
            return spec
    raise ValueError(f"未知角色伤害模型：{key}")


def _token(value: object) -> str:
    return "".join(ch for ch in str(value).casefold() if ch.isalnum())


@lru_cache(maxsize=256)
def _english_weapon_name(value: str) -> str | None:
    try:
        from data.references import get_game_record
        record = get_game_record("weapons", value)
        english = get_game_record("weapons", record["id"], language="en")
        return str(english.get("name") or "")
    except (KeyError, TypeError, ValueError):
        return None


def weapon_matches(value: object, expected: object) -> bool:
    """Match a weapon name or ID against a canonical English weapon name."""
    if value is None or expected is None:
        return value is None and expected is None
    if _token(value) == _token(expected):
        return True
    english = _english_weapon_name(str(value))
    return english is not None and _token(english) == _token(expected)


def _validate_catalog_options(key: str, spec: Mapping[str, object], crit_mode: str, options: Mapping[str, object]) -> None:
    if crit_mode not in ("average", "noncrit"):
        raise ValueError("crit_mode 只能是 average 或 noncrit")
    unknown = set(options) - {
        "level", "ascension", "constellation", "talents", "weapon",
        "weapon_level", "weapon_ascension", "refinement",
    }
    if unknown:
        raise ValueError(f"模型 {key} 不支持参数：{', '.join(sorted(unknown))}")
    assumptions = dict(spec.get("assumptions") or {})
    expected_level = int(assumptions.get("character_level", 90))
    expected_ascension = int(assumptions.get("ascension", 6))
    expected_constellation = int(assumptions.get("constellation", 0))
    expected_talents = assumptions.get("talents") or {"auto": 10, "skill": 10, "burst": 10}
    if "level" in options and int(options["level"]) != expected_level:
        raise ValueError(f"模型 {key} 固定在 {expected_level} 级")
    if "ascension" in options and int(options["ascension"]) != expected_ascension:
        raise ValueError(f"模型 {key} 固定在突破 {expected_ascension}")
    if "constellation" in options and int(options["constellation"]) != expected_constellation:
        raise ValueError(f"模型 {key} 固定在 C{expected_constellation}")
    if "talents" in options and dict(options["talents"]) != expected_talents:
        raise ValueError(f"模型 {key} 固定在天赋 {expected_talents}")
    fixed_weapon = spec.get("weapon")
    if fixed_weapon is not None and "weapon" in options:
        if not weapon_matches(options["weapon"], fixed_weapon):
            raise ValueError(f"模型 {key} 只覆盖武器 {fixed_weapon}")


@lru_cache(maxsize=2048)
def _cached_catalog_model(key: str, crit_mode: str) -> DamageModel:
    return model_from_spec(catalog_spec(key), crit_mode)


def get_catalog_model(key: str, crit_mode: str = "average", **options) -> DamageModel:
    spec = catalog_spec(key)
    _validate_catalog_options(key, spec, crit_mode, options)
    return _cached_catalog_model(key, crit_mode)


def default_catalog_key(character: str) -> str | None:
    token = character.strip().casefold()
    for spec in catalog_models():
        if str(spec.get("character", "")).casefold() == token:
            return str(spec["key"])
    return None


def catalog_available() -> dict:
    result = {}
    for spec in catalog_models():
        result[str(spec["key"])] = {
            "character": spec.get("character"),
            "character_zh": spec.get("character_zh"),
            "weapon_type": spec.get("weapon_type"),
            "label": spec.get("label"),
            "element": spec.get("element"),
            "attack": spec.get("attack"),
            "category": spec.get("category"),
            "target_four_piece_set": spec.get("target_four_piece_set"),
            "accuracy": spec.get("accuracy"),
            "max_relative_error": (spec.get("calibration") or {}).get("max_relative_error"),
        }
    return result
