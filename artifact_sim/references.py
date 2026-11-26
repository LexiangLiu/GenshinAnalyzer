"""Versioned, read-only game reference data, loaded one category at a time."""

from __future__ import annotations

import gzip
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Mapping


_ROOT = Path(__file__).with_name("reference_data")


@lru_cache(maxsize=1)
def _manifest() -> dict:
    return json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))


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
    key = _query_key(category, query, language)
    return {"key": key, **deepcopy(_rows(category, language)[key])}


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
        name = str(row.get("name", key))
        if version is not None and row.get("version") != version:
            continue
        if filters and any(field not in row or row[field] != value for field, value in filters.items()):
            continue
        if token and not any(token in str(value).casefold() for value in (
            key, name, row.get("id", ""), alternate.get(key, {}).get("name", ""),
        )):
            continue
        matching.append({
            "key": key, "id": row.get("id"), "name": name, "version": row.get("version"),
            **{field: row[field] for field in ("rarity", "weaponType", "elementType", "category", "regionName") if field in row},
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
        if key in ("aether", "lumine"):
            keys = [entry for entry in records if entry.startswith("traveler")]
        else:
            keys = [key] if key in records else []
        collections[folder] = [{"key": entry, **deepcopy(records[entry])} for entry in keys]
    return {"character": character, **collections}


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
