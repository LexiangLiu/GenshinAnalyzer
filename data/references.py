"""Versioned, read-only game reference data, loaded one category at a time."""

from __future__ import annotations

import gzip
import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Mapping


_ROOT = Path(__file__).with_name("reference_data")

_TRAVELER_ELEMENTS = {
    "anemo": ("风", "Anemo"),
    "geo": ("岩", "Geo"),
    "electro": ("雷", "Electro"),
    "dendro": ("草", "Dendro"),
    "hydro": ("水", "Hydro"),
    "pyro": ("火", "Pyro"),
    "cryo": ("冰", "Cryo"),
}
_TRAVELER_APPEARANCES = {"aether": 10000005, "lumine": 10000007}
_MANEKIN_APPEARANCES = {"manekin": 10000117, "manekina": 10000118}
_MANEKIN_ABILITIES = {
    "zh": (
        ("普通攻击", "Outlander Arts / Vigilant Watch", "奇偶的普通攻击。"),
        ("元素战技", "Punishing Barrage", "增加与敌人的距离，并使普通攻击、重击和下落攻击转为奇偶当前元素伤害，且不能被其他元素附魔覆盖。"),
        ("元素爆发", "No Entry", "创建一个领域，敌人进入时受到伤害。"),
        ("被动天赋", "Battlelines Redrawn", "离场时，领域爆炸并造成相当于攻击力 200% 的当前元素伤害。"),
        ("被动天赋", "Combat Readiness", "离场且元素能量低于 30% 时，每秒恢复 2 点元素能量。"),
        ("被动天赋", "Agile Stance", "施放元素战技或元素爆发时，随机切换至已有的换装。"),
    ),
    "en": (
        ("Normal Attack", "Outlander Arts / Vigilant Watch", "Manekin's normal attack."),
        ("Elemental Skill", "Punishing Barrage", "Creates distance from enemies and converts Normal, Charged, and Plunging Attacks to the current Adaptive Element; this infusion cannot be overridden."),
        ("Elemental Burst", "No Entry", "Creates a field that deals damage when an enemy enters it."),
        ("Passive", "Battlelines Redrawn", "On leaving the field, the field explodes and deals current-element damage equal to 200% of ATK."),
        ("Passive", "Combat Readiness", "While off-field and below 30% Energy, restores 2 Energy per second."),
        ("Passive", "Agile Stance", "Casting an Elemental Skill or Burst randomly switches to an owned outfit."),
    ),
}
_MANEKIN_UNCALIBRATED = {
    "zh": "离线资料已包含七元素形态的天赋倍率与技能参数；固定版 Genshin Optimizer 没有奇偶公式节点，因此尚未校准，不参与配装评分。",
    "en": "Offline talent multipliers and skill parameters are available for all seven elements. The pinned Genshin Optimizer bundle has no Manekin formula node, so the character remains uncalibrated and excluded from loadout scoring.",
}


@lru_cache(maxsize=1)
def _manifest() -> dict:
    return json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _manekin_combat() -> dict:
    metadata = _manifest().get("supplements", {}).get("manekin_combat")
    if not metadata:
        raise RuntimeError("离线图鉴缺少奇偶战斗参数补充数据")
    path = _ROOT / metadata["file"]
    packed = path.read_bytes()
    if hashlib.sha256(packed).hexdigest() != metadata["sha256"]:
        raise RuntimeError("奇偶战斗参数快照与清单校验值不符")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if set(payload.get("elements", {})) != set(_TRAVELER_ELEMENTS):
        raise RuntimeError("奇偶战斗参数快照的元素形态不完整")
    return payload


def game_data_info() -> dict:
    """Return the game version, provenance, counts and available languages."""
    return deepcopy(_manifest())


@lru_cache(maxsize=12)
def _read(name: str) -> dict:
    with gzip.open(_ROOT / name, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _rows(category: str, language: str) -> dict:
    if category not in _manifest()["categories"]:
        raise ValueError(f"未知游戏资料类别：{category}")
    if language not in _manifest()["languages"]:
        raise ValueError(f"未知语言：{language}，可用语言为 zh、en")
    source_language = _manifest()["categories"][category]["source_languages"][language]
    return _read(f"{category}.{source_language}.json.gz")


def _name_token(value: object) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


@lru_cache(maxsize=2)
def _traveler_forms(language: str) -> tuple[dict, ...]:
    talents = _rows("talents", language)
    constellations = _rows("constellations", language)
    zh_talents = _rows("talents", "zh")
    en_talents = _rows("talents", "en")
    forms = []
    for key in sorted(set(talents) & set(constellations)):
        if not key.startswith("traveler"):
            continue
        element = key.removeprefix("traveler")
        localized = _TRAVELER_ELEMENTS.get(element)
        if localized is None:
            continue
        zh_element, en_element = localized
        name = f"旅行者·{zh_element}"
        name_en = f"Traveler ({en_element})"
        aliases = {
            key,
            name,
            name_en,
            f"Traveler {en_element}",
            f"旅行者 ({zh_element}元素)",
            f"旅行者（{zh_element}元素）",
            zh_talents[key].get("name", ""),
            en_talents[key].get("name", ""),
        }
        forms.append({
            "key": key,
            "element": element,
            "elementType": f"ELEMENT_{element.upper()}",
            "element_name": zh_element if language == "zh" else en_element,
            "name": name if language == "zh" else name_en,
            "name_zh": name,
            "name_en": name_en,
            "version": talents[key].get("version"),
            "aliases": tuple(alias for alias in aliases if alias),
        })
    return tuple(forms)


def traveler_forms(*, language: str = "zh") -> list[dict]:
    """Return only Traveler element forms with both talent and constellation records."""
    return [dict(form) for form in _traveler_forms(language)]


def traveler_form(query: object, *, language: str = "en") -> dict | None:
    """Resolve a Traveler element name or reference key to its offline form metadata."""
    token = _name_token(query)
    if not token:
        return None
    for form in _traveler_forms(language):
        if token in {_name_token(alias) for alias in form["aliases"]}:
            return dict(form)
    return None


def traveler_form_for_element(element: object) -> dict | None:
    """Resolve a bare element option used by account generation to its Traveler form."""
    token = _name_token(element)
    if not token:
        return None
    for form in _traveler_forms("en"):
        if token in {
            _name_token(form["element"]),
            _name_token(_TRAVELER_ELEMENTS[form["element"]][0]),
            _name_token(_TRAVELER_ELEMENTS[form["element"]][1]),
        }:
            return dict(form)
    return None


def is_traveler_identity(query: object) -> bool:
    """Whether a query denotes Traveler, including either appearance or any element form."""
    token = _name_token(query)
    if token in {"traveler", "aether", "lumine", "10000005", "10000007", "空", "荧"}:
        return True
    return traveler_form(query) is not None or traveler_form(query, language="zh") is not None


def manekin_appearance(query: object) -> str | None:
    """Resolve a Manekin appearance key, ID, or localized name to its shared identity."""
    token = _name_token(query)
    if not token:
        return None
    for key, character_id in _MANEKIN_APPEARANCES.items():
        aliases = {
            key,
            str(character_id),
            _rows("characters", "en")[key].get("name", ""),
            _rows("characters", "zh")[key].get("name", ""),
        }
        if token in {_name_token(alias) for alias in aliases if alias}:
            return key
    if token in {_name_token("manekin"), _name_token("奇偶")}:
        return "manekin"
    return None


def is_manekin_identity(query: object) -> bool:
    """Whether a query denotes the shared Manekin/Manekina combat identity."""
    return manekin_appearance(query) is not None


def party_exclusive_identity(query: object) -> str | None:
    """Return the identity group that must not be duplicated in one party."""
    if is_traveler_identity(query):
        return "traveler"
    if is_manekin_identity(query):
        return "manekin"
    return None


def _manekin_record(appearance_key: str | None, language: str) -> dict:
    appearances = _rows("characters", language)
    source = deepcopy(appearances[appearance_key or "manekin"])
    source.pop("gender", None)
    source.pop("bodyType", None)
    source["key"] = "manekin"
    source["identity"] = "manekin"
    source["name"] = "奇偶" if language == "zh" else "Manekin / Manekina"
    source["name_en"] = "Manekin / Manekina"
    source["elementType"] = "ELEMENT_ADAPTIVE"
    source["elementText"] = "跟随旅行者当前元素" if language == "zh" else "Adaptive (follows Traveler)"
    source["appearance_ids"] = list(_MANEKIN_APPEARANCES.values())
    source["appearances"] = [
        {
            "key": key,
            "id": character_id,
            "name": appearances[key]["name"],
            "gender": appearances[key].get("gender"),
        }
        for key, character_id in _MANEKIN_APPEARANCES.items()
    ]
    source["active_appearance"] = appearance_key
    source.pop("costs", None)
    source["battle_support"] = "uncalibrated"
    source["battle_model_status"] = _MANEKIN_UNCALIBRATED[language]
    source["battle_exclusion_reason"] = source["battle_model_status"]
    source["talent_multipliers_available"] = True
    source["combat_parameter_elements"] = list(_manekin_combat()["elements"])
    source["ability_source"] = (
        "7.2 原始角色技能表；普通攻击、战技、爆发与被动参数经固定版本 genshin-data 角色表交叉核对。"
        if language == "zh" else
        "Version 7.2 raw character skill tables; normal attack, skill, burst, and passive parameters cross-checked against a pinned genshin-data character snapshot."
    )
    source["activity_role"] = (
        "提瓦特战斗角色；千星奇域内可切换外观"
        if language == "zh" else
        "Teyvat combat character; appearance can be switched in Miliastra Wonderland"
    )
    return source


def _traveler_record(key: str | None, language: str) -> dict:
    appearances = _rows("characters", language)
    source = deepcopy(appearances["aether"])
    source.pop("gender", None)
    source.pop("bodyType", None)
    forms = _traveler_forms(language)
    source["key"] = key or "traveler"
    source["id"] = _TRAVELER_APPEARANCES["aether"]
    source["appearance_ids"] = list(_TRAVELER_APPEARANCES.values())
    source["traveler_identity"] = "traveler"
    source["element_forms"] = [form["key"] for form in forms]
    if key is None:
        source["name"] = "旅行者" if language == "zh" else "Traveler"
        source["name_en"] = "Traveler"
        source["elementType"] = "ELEMENT_NONE"
        source["elementText"] = "无" if language == "zh" else "None"
    else:
        selected = next(form for form in forms if form["key"] == key)
        source["name"] = selected["name"]
        source["name_en"] = selected["name_en"]
        source["elementType"] = selected["elementType"]
        source["elementText"] = selected["element_name"]
        source["traveler_form"] = key
        source["version"] = selected["version"]
    return source


def _special_character_record(category: str, query: str | int, language: str) -> dict | None:
    if category != "characters":
        return None
    token = _name_token(query)
    if token in {"traveler", _name_token("旅行者")}:
        return _traveler_record(None, language)
    form = traveler_form(query, language=language)
    if form:
        return _traveler_record(form["key"], language)
    appearance = manekin_appearance(query)
    return _manekin_record(appearance, language) if appearance else None


def _annotate_character(record: dict, language: str) -> dict:
    key = str(record.get("key", "")).casefold()
    if key in _TRAVELER_APPEARANCES:
        record["traveler_identity"] = "traveler"
    if key in _MANEKIN_APPEARANCES:
        record["identity"] = "manekin"
        record["battle_support"] = "uncalibrated"
        record["battle_model_status"] = _MANEKIN_UNCALIBRATED[language]
        record["battle_exclusion_reason"] = record["battle_model_status"]
        record["activity_role"] = (
            "提瓦特战斗角色；千星奇域内可切换外观"
            if language == "zh" else
            "Teyvat combat character; appearance can be switched in Miliastra Wonderland"
        )
    return record


def _query_key(category: str, query: str | int, language: str) -> str:
    rows = _rows(category, language)
    token = str(query).strip().casefold()
    if not token:
        raise ValueError("查询不能为空")
    if token in rows:
        return token
    matches = [key for key, row in rows.items() if "id" in row and str(row["id"]) == token]
    if not matches:
        names = _rows(category, "en" if language == "zh" else "zh")
        matches = [
            key for key, row in rows.items()
            if token in (str(row.get("name", "")).casefold(), str(names.get(key, {}).get("name", "")).casefold())
        ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        choices = ", ".join(f"{key} ({rows[key].get('id', key)})" for key in matches)
        raise ValueError(f"名称有多个匹配，请使用 ID 或资料键：{choices}")
    raise ValueError(f"未找到 {category} 记录：{query}")


def get_game_record(category: str, query: str | int, *, language: str = "zh") -> dict:
    """Read one record by upstream key, game ID, or exact name in either language."""
    special = _special_character_record(category, query, language)
    if special is not None:
        return special
    key = _query_key(category, query, language)
    return _annotate_character(
        {"key": key, **deepcopy(_rows(category, language)[key])}, language,
    )


def list_game_records(
    category: str, *, language: str = "zh", search: str | None = None,
    version: str | None = None, filters: Mapping[str, object] | None = None,
    limit: int | None = None, offset: int = 0,
) -> list[dict]:
    """List concise records, with substring, version and exact top-level field filters."""
    if type(offset) is not int or offset < 0 or (limit is not None and (type(limit) is not int or limit < 0)):
        raise ValueError("offset 和 limit 必须为非负整数")
    rows = _rows(category, language)
    alternate = _rows(category, "en" if language == "zh" else "zh") if search else {}
    token = search.strip().casefold() if search else ""
    matching = []
    for key, row in rows.items():
        if category == "characters" and key in _MANEKIN_APPEARANCES:
            continue
        name = str(row.get("name", key))
        if version is not None and row.get("version") != version:
            continue
        if filters and any(field not in row or row[field] != value for field, value in filters.items()):
            continue
        if token and not any(token in str(value).casefold() for value in (
            key, name, row.get("id", ""), alternate.get(key, {}).get("name", ""),
        )):
            continue
        summary = {
            "key": key, "id": row.get("id"), "name": name, "version": row.get("version"),
            **{field: row[field] for field in ("rarity", "weaponType", "elementType", "category", "regionName") if field in row},
        }
        if category == "characters" and key in _TRAVELER_APPEARANCES:
            summary["identity"] = "traveler"
        matching.append(summary)
    if category == "characters":
        synthetic = [_traveler_record(None, language)]
        synthetic.extend(_traveler_record(form["key"], language) for form in _traveler_forms(language))
        for row in synthetic:
            name = row["name"]
            if version is not None and row.get("version") != version:
                continue
            if filters and any(field not in row or row[field] != value for field, value in filters.items()):
                continue
            if token and not any(token in str(value).casefold() for value in (
                row["key"], name, row.get("name_en", ""), row.get("elementType", ""),
            )):
                continue
            matching.append({
                "key": row["key"], "id": row.get("id"), "name": name,
                "version": row.get("version"),
                "identity": "traveler",
                **{field: row[field] for field in ("rarity", "weaponType", "elementType", "category", "regionName") if field in row},
            })
        puppet = _manekin_record(None, language)
        appearance_names = [
            _rows("characters", localized_language)[key]["name"]
            for localized_language in ("zh", "en")
            for key in _MANEKIN_APPEARANCES
        ]
        if (
            (version is None or puppet.get("version") == version)
            and not (filters and any(field not in puppet or puppet[field] != value for field, value in filters.items()))
            and (
                not token
                or any(token in str(value).casefold() for value in (
                    puppet["key"], puppet["name"], puppet["name_en"], "奇偶", *appearance_names,
                    *(appearance["key"] for appearance in puppet["appearances"]),
                ))
            )
        ):
            matching.append({
                "key": "manekin",
                "id": puppet["id"],
                "name": puppet["name"],
                "name_en": puppet["name_en"],
                "version": puppet.get("version"),
                "identity": "manekin",
                "appearances": puppet["appearances"],
                "battle_support": "uncalibrated",
                "battle_model_status": puppet["battle_model_status"],
                "battle_exclusion_reason": puppet["battle_exclusion_reason"],
                "activity_role": puppet["activity_role"],
                **{field: puppet[field] for field in ("rarity", "weaponType", "elementType") if field in puppet},
            })
    end = None if limit is None else offset + limit
    return matching[offset:end]


def get_character_profile(query: str | int, *, language: str = "zh") -> dict:
    """Read a character with linked talent and constellation records."""
    character = get_game_record("characters", query, language=language)
    key = character["key"]
    folders = ("talents", "constellations")
    collections = {}
    for folder in folders:
        records = _rows(folder, language)
        if key == "traveler":
            keys = [entry for entry in records if entry.startswith("traveler")]
        elif key in ("aether", "lumine"):
            keys = [entry for entry in records if entry.startswith("traveler")]
        elif key.startswith("traveler"):
            keys = [key] if key in records else []
        else:
            keys = [key] if key in records else []
        collections[folder] = [{"key": entry, **deepcopy(records[entry])} for entry in keys]
    profile = {"character": _annotate_character(character, language), **collections}
    if is_traveler_identity(query):
        profile["identity"] = "traveler"
        profile["active_form"] = key if key.startswith("traveler") else None
    elif is_manekin_identity(query):
        profile.update({
            "identity": "manekin",
            "abilities": [
                {"category": category, "name": name, "summary": summary}
                for category, name, summary in _MANEKIN_ABILITIES[language]
            ],
            "combat_data": deepcopy(_manekin_combat()),
            "battle_model_status": _MANEKIN_UNCALIBRATED[language],
            "talent_multipliers_available": True,
            "damage_models_calibrated": False,
            "constellations_available": False,
            "ability_source": character["ability_source"],
        })
    return profile


def get_game_stats(
    category: str, query: str | int, level: int, *,
    ascension: str | int = "before", language: str = "zh",
) -> dict:
    """Read a base stat row. At an ascension boundary, select either side or an exact phase."""
    if category not in _manifest()["stats"]:
        raise ValueError(f"该类别没有等级属性数据：{category}")
    if type(level) is not int or level < 1:
        raise ValueError("等级必须为正整数")
    record = get_game_record(category, query, language=language)
    levels = _read(f"{category}.stats.json.gz")[str(record["id"])]
    available = levels.get(str(level))
    if not available:
        raise ValueError(f"{record['name']} 的属性等级范围是 1–{max(map(int, levels))}")
    if ascension in ("before", "after"):
        stats = available[0 if ascension == "before" else -1]
    else:
        stats = next((row for row in available if type(ascension) is int and row.get("ascension") == ascension), None)
        if stats is None:
            raise ValueError(f"{record['name']} 在 {level} 级没有突破阶段 {ascension}")
    result = {"id": record["id"], "name": record["name"], **stats}
    if category in ("characters", "weapons"):
        result["specialized_type"] = record.get("substatType" if category == "characters" else "mainStatType")
    return result
