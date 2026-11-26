from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


DATA = json.loads(Path(__file__).with_name("data.json").read_text(encoding="utf-8"))
SETS = {item["id"]: item for item in DATA["sets"]}
DOMAINS = {item["id"]: item for item in DATA["domains"]}
CHARACTERS = {item["id"]: item for item in DATA["characters"]}
MAIN_VALUES = DATA["main_stat_values"]
SUB_VALUES = DATA["substat_values"]


def catalog() -> dict:
    """Return the dataset version, set metadata and five-star artifact domains."""
    return deepcopy({
        key: DATA[key] for key in ("game_version", "sources", "sets", "domains", "characters", "stat_units")
    })


def lookup(items: dict, name: str | int) -> dict:
    if isinstance(name, (int, str)) and str(name).isdigit():
        match = items.get(int(name))
        if match is not None:
            return match
    elif isinstance(name, str) and name.strip():
        for item in items.values():
            if name.strip().casefold() in (item["name"].casefold(), item.get("name_en", "").casefold()):
                return item
    raise ValueError(f"未知的套装或秘境：{name}")


def set_id(name: str | int) -> int:
    return lookup(SETS, name)["id"]


def character_profile(name: str) -> str:
    """Resolve a known character's names or ID to one key, or accept a custom profile."""
    name = name.strip()
    if not name:
        raise ValueError("配装名称不能为空")
    key = "".join(char for char in name.casefold() if char.isalnum())
    for data in CHARACTERS.values():
        aliases = (data["name"], data["name_en"])
        if name == str(data["id"]) or key in {
            "".join(char for char in alias.casefold() if char.isalnum()) for alias in aliases
        }:
            return data["name_en"]
    return name


def characters(*, element=None, weapon=None, region=None):
    return [
        dict(item) for item in CHARACTERS.values()
        if (element is None or item.get("element") == element.upper())
        and (weapon is None or item.get("weapon") == weapon.upper())
        and (region is None or region.casefold() in ((item["region"] or "").casefold(), (item["region_en"] or "").casefold()))
    ]
