from __future__ import annotations

import argparse
from html import escape
import json
from math import isclose
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from data.references import (
    get_character_profile,
    get_game_record,
    is_manekin_identity,
    list_game_records,
    party_exclusive_identity,
    traveler_form,
    traveler_form_for_element,
    traveler_forms,
)
from damage import damage_engine_info, list_damage_formulas
from damage.character_models import catalog_spec
from damage.damage_models import (
    available_models,
    evaluate_formula_breakdown,
    get_damage_model,
)
from inventory import Inventory
from tools.simulate_account import (
    MAIN_ROSTER,
    MAX_ARTIFACT_INVENTORY,
    SLOTS,
    available_character_count,
    validate_artifact_count,
)
from data.game_data import SETS


ROOT = Path(__file__).resolve().parents[1]
PAGE = Path(__file__).with_name("acceptance_ui.html")
MAX_SAFE_INTEGER = 2**53 - 1
DETAIL_PAGE_SIZE = 24


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} 必须是整数")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} 必须介于 {minimum} 和 {maximum} 之间")
    return value


def validate_generation(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("请求内容必须是 JSON 对象")
    traveler_element = payload.get("traveler_element")
    if traveler_element in ("", None):
        traveler_element = None
    else:
        form = (
            traveler_form(traveler_element)
            if isinstance(traveler_element, str) else None
        ) or traveler_form_for_element(traveler_element)
        if form is None:
            raise ValueError("旅行者元素形态无效")
        traveler_element = form["element"]
    return {
        "characters": _integer(
            payload.get("characters", min(8, available_character_count())),
            "角色数量",
            min(available_character_count(), 1 + int(bool(traveler_forms(language="en"))) + 1),
            available_character_count(),
        ),
        "artifacts": validate_artifact_count(payload.get("artifacts", 180)),
        "seed": _integer(payload.get("seed", 42), "随机种子", -MAX_SAFE_INTEGER, MAX_SAFE_INTEGER),
        "traveler_element": traveler_element,
    }


def optimizer_roster_size(character_count: int, artifact_count: int) -> int:
    artifacts_per_preset_roster = len(MAIN_ROSTER) * len(SLOTS)
    return min(
        max(1, character_count - int(bool(traveler_forms(language="en"))) - 1),
        len(MAIN_ROSTER),
        max(1, artifact_count // artifacts_per_preset_roster),
    )


def build_selected_request(
    base_request: dict[str, Any],
    selected_characters: object,
    mode: object,
    seed: object,
    max_nodes: object,
    domains: object = None,
    fixed_four_piece_sets: object = None,
) -> dict[str, Any]:
    if not isinstance(selected_characters, list) or not selected_characters:
        raise ValueError("至少选择一个角色")
    if any(not isinstance(name, str) or not name for name in selected_characters):
        raise ValueError("角色选择无效")
    if len(set(selected_characters)) != len(selected_characters):
        raise ValueError("不能重复选择同一角色")
    identity_counts = {
        identity: sum(party_exclusive_identity(name) == identity for name in selected_characters)
        for identity in ("traveler", "manekin")
    }
    if identity_counts["traveler"] > 1:
        raise ValueError("一个队伍只能包含一个旅行者身份；空、荧及不同元素形态互斥")
    if identity_counts["manekin"] > 1:
        raise ValueError("一个队伍只能包含一个奇偶身份；男性与女性外观互斥")
    if any(is_manekin_identity(name) for name in selected_characters):
        raise ValueError("奇偶战斗模型尚未校准，不参与配装评分")
    if mode == "single" and len(selected_characters) != 1:
        raise ValueError("单角色模式只能选择一个角色")
    if mode == "multi" and len(selected_characters) < 2:
        raise ValueError("多角色模式至少选择两个角色")
    if mode not in ("single", "multi"):
        raise ValueError("优化模式无效")

    available = base_request.get("members", [])
    by_character = {
        member.get("character"): member
        for member in available
        if isinstance(member, dict) and isinstance(member.get("character"), str)
    }
    unknown = [name for name in selected_characters if name not in by_character]
    if unknown:
        raise ValueError(f"所选角色不在可优化角色列表中：{', '.join(unknown)}")

    available_domains = base_request.get("domains", [])
    domain_ids = []
    for domain in available_domains:
        domain_id = domain.get("id") if isinstance(domain, dict) else domain
        if isinstance(domain_id, int) and not isinstance(domain_id, bool):
            domain_ids.append(domain_id)
    valid_domains = set(domain_ids)
    if domains is None:
        selected_domains = domain_ids
    else:
        if not isinstance(domains, list) or not domains:
            raise ValueError("至少选择一个规划秘境")
        if any(isinstance(domain, bool) or not isinstance(domain, int) for domain in domains):
            raise ValueError("秘境选择无效")
        if len(set(domains)) != len(domains):
            raise ValueError("不能重复选择同一秘境")
        if any(domain not in valid_domains for domain in domains):
            raise ValueError("所选秘境不在当前账号的可用列表中")
        selected_domains = domains

    request = dict(base_request)
    request["members"] = [dict(by_character[name]) for name in selected_characters]
    if fixed_four_piece_sets is not None:
        if not isinstance(fixed_four_piece_sets, dict):
            raise ValueError("固定套装选择无效")
        if any(not isinstance(name, str) or name not in selected_characters
               for name in fixed_four_piece_sets):
            raise ValueError("固定套装只能为本次选择的角色设置")
        valid_fixed_sets = {
            set_id for set_id, info in SETS.items()
            if 5 in info["rarities"] and "4" in info["effects"]
        }
        for member in request["members"]:
            character = member["character"]
            fixed_set = fixed_four_piece_sets.get(character)
            if fixed_set is None:
                member.pop("fixed_four_piece_set", None)
            elif type(fixed_set) is not int or fixed_set not in valid_fixed_sets:
                raise ValueError(f"{character} 的固定套装必须是有效的五星四件套")
            else:
                member["fixed_four_piece_set"] = fixed_set
    request["domains"] = selected_domains
    request["seed"] = _integer(seed, "优化种子", -MAX_SAFE_INTEGER, MAX_SAFE_INTEGER)
    request["max_nodes"] = _integer(max_nodes, "搜索节点上限", 1000, 2_000_000)
    return request


def _run(command: list[str], timeout: int = 600) -> None:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    try:
        process = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"命令执行超时（{timeout} 秒），请缩小账号或搜索规模后重试") from exc
    if process.returncode:
        output = "\n".join(part.strip() for part in (process.stdout, process.stderr) if part.strip())
        raise RuntimeError(output[-6000:] or f"命令失败，退出码 {process.returncode}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取现有工具的结果：{path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"现有工具返回了无效结果：{path.name}")
    return value


def _public_member(member: dict[str, Any]) -> dict[str, Any]:
    loadout = member.get("loadout") or {}
    pieces = []
    for piece in loadout.get("pieces", []):
        state = piece.get("state") or {}
        pieces.append(
            {
                "slot": piece.get("slot"),
                "id": piece.get("id"),
                "set_name": piece.get("set_name"),
                "piece_name": piece.get("piece_name"),
                "level": state.get("level"),
                "main_stat": state.get("main_stat"),
                "main_value": piece.get("main_value"),
                "active_substats": piece.get("active_substats", {}),
                "score": piece.get("score"),
                "crit_value": piece.get("crit_value"),
                "expected_full_level": piece.get("expected_full_level"),
            }
        )
    return {
        "name": member.get("name"),
        "character": member.get("character"),
        "model": member.get("model"),
        "weight": member.get("weight"),
        "target_four_piece_set": member.get("target_four_piece_set"),
        "damage": member.get("damage"),
        "reference_damage": member.get("reference_damage"),
        "normalized_damage": member.get("normalized_damage"),
        "score": member.get("score"),
        "pieces": pieces,
    }


def format_formula_expression(expression: str | None) -> str | None:
    if not expression:
        return None

    lines = []
    current = []
    for character in expression:
        if character == "*":
            lines.append("".join(current).strip())
            current = ["* "]
            continue
        current.append(character)
    lines.append("".join(current).strip())
    return "\n".join(line for line in lines if line)


_FORMULA_STAT_LABELS = {
    "HP": "生命值",
    "ATK": "攻击力",
    "DEF": "防御力",
    "HP_PERCENT": "生命值百分比",
    "ATK_PERCENT": "攻击力百分比",
    "DEF_PERCENT": "防御力百分比",
    "ELEMENTAL_MASTERY": "元素精通",
    "ENERGY_RECHARGE": "元素充能效率",
    "CRIT_RATE": "暴击率",
    "CRIT_DMG": "暴击伤害",
    "HEALING_BONUS": "治疗加成",
    "PYRO_DMG_BONUS": "火元素伤害加成",
    "ELECTRO_DMG_BONUS": "雷元素伤害加成",
    "HYDRO_DMG_BONUS": "水元素伤害加成",
    "ANEMO_DMG_BONUS": "风元素伤害加成",
    "GEO_DMG_BONUS": "岩元素伤害加成",
    "DENDRO_DMG_BONUS": "草元素伤害加成",
    "CRYO_DMG_BONUS": "冰元素伤害加成",
    "PHYSICAL_DMG_BONUS": "物理伤害加成",
}

_TALENT_LABELS = {
    "auto": "普通攻击",
    "skill": "元素战技",
    "burst": "元素爆发",
}

_ATTACK_LABELS = {
    "auto": "普通攻击",
    "normal": "普通攻击",
    "normal_0": "普通攻击第 1 段",
    "normal_1": "普通攻击第 2 段",
    "normal_2": "普通攻击第 3 段",
    "normal_3": "普通攻击第 4 段",
    "charged": "重击",
    "charged_judgment": "重击单段",
    "plunge": "下落攻击",
    "plunging": "下落攻击",
    "plunging_high": "下落攻击（高空）",
    "skill": "元素战技",
    "skill_hold": "元素战技（长按）",
    "skill_blossom": "元素战技伤害",
    "burst": "元素爆发",
    "burst_waterfall": "元素爆发连续伤害",
}

_ELEMENT_LABELS = {
    "PYRO": "火元素",
    "HYDRO": "水元素",
    "ANEMO": "风元素",
    "ELECTRO": "雷元素",
    "DENDRO": "草元素",
    "CRYO": "冰元素",
    "GEO": "岩元素",
    "PHYSICAL": "物理",
}

_INTERNAL_FORMULA_TOKEN = re.compile(
    r"\b(?:HP|ATK|DEF|ELEMENTAL_MASTERY|ENERGY_RECHARGE|CRIT_RATE|CRIT_DMG|"
    r"[A-Z]+_DMG_BONUS|SET[_-]?\d+(?:[_-]\d+)?)\b|"
    r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b",
    re.IGNORECASE,
)


def _formula_set_label(term: Mapping[str, Any]) -> str:
    try:
        set_id = int(term["set_id"])
        pieces = int(term["pieces"])
    except (KeyError, TypeError, ValueError):
        return "未命名效果"
    name = SETS.get(set_id, {}).get("name")
    if not isinstance(name, str) or not name.strip():
        return f"未命名效果（{pieces}件套）"
    return f"{name}{pieces}件套"


def _formula_term_label(term: Mapping[str, Any], context: str | None = None) -> str:
    kind = term.get("kind")
    if kind == "stat":
        label = _FORMULA_STAT_LABELS.get(str(term["stat"]), "角色属性")
        coefficient = float(term.get("coefficient", 1.0))
        return label if coefficient == 1 else f"{label} × {coefficient:.4g}"
    if kind == "constant":
        return f"固定伤害值 {float(term['value']):.4g}"
    if kind == "constant_percent":
        source = term.get("source_label")
        if not isinstance(source, str) or not source.strip() or _INTERNAL_FORMULA_TOKEN.search(source):
            source = {
                "damage": "角色或模型固定伤害加成",
                "crit_rate": "固定暴击率加成",
                "crit_dmg": "固定暴击伤害加成",
            }.get(context, "模型固定加成")
        return f"{source} +{float(term['value']):g}%"
    if kind == "set_stat_percent":
        stat = str(term["base_stat"]).removeprefix("BASE_")
        stat_label = _FORMULA_STAT_LABELS.get(stat, "角色属性")
        return f"{_formula_set_label(term)}提供的{stat_label} +{float(term.get('value', 0)):g}%"
    if kind == "set_stat_flat":
        return f"{_formula_set_label(term)}提供的元素精通 +{float(term.get('value', 0)):g}"
    if kind == "set_percent":
        label = {
            "damage": "伤害加成",
            "crit_rate": "暴击率加成",
            "crit_dmg": "暴击伤害加成",
        }.get(context, "加成")
        return f"{_formula_set_label(term)}{label} +{float(term['value']):g}%"
    if kind == "set_er_percent":
        return (
            f"{_formula_set_label(term)}按元素充能效率转化伤害加成"
            f"（每 1% 充能提高 {float(term['value']):g}%，"
            f"最多 {float(term['cap']):g}%）"
        )
    return "未命名评分因子"


def _readable_formula(model) -> str | None:
    factors = model.formula_factors
    if not factors:
        return None

    scaling = " + ".join(
        _formula_term_label(term) for term in factors.get("scaling_terms", [])
    )
    if not scaling:
        scaling = "角色伤害属性"
    parts = [f"伤害基数（{scaling}）"]
    talent = factors.get("talent")
    if isinstance(talent, dict):
        talent_name = _TALENT_LABELS.get(talent.get("slot"), "角色技能")
        parts.append(
            f"{talent_name}倍率（天赋等级 {talent.get('level')}，"
            f"相对标定等级 ×{float(talent.get('multiplier', 1)):.4g}）"
        )
    damage_terms = [
        _formula_term_label(term, "damage") for term in factors.get("damage_terms", [])
    ]
    if damage_terms:
        parts.append(f"伤害加成系数（1 +（{' + '.join(damage_terms)}）÷ 100）")
    if factors.get("crit_enabled"):
        rate_terms = [
            _formula_term_label(term, "crit_rate")
            for term in factors.get("crit_rate_terms", [])
        ]
        dmg_terms = [
            _formula_term_label(term, "crit_dmg")
            for term in factors.get("crit_dmg_terms", [])
        ]
        parts.append(
            "暴击期望系数（1 + min(100%, "
            + " + ".join(rate_terms)
            + ") × ("
            + " + ".join(dmg_terms)
            + ") ÷ 10000）"
        )
    for _multiplier in factors.get("fixed_multipliers", []):
        parts.append(
            f"角色固有机制系数 × {float(_multiplier['value']):.4g}"
        )
    if model.scale != 1:
        talent_multiplier = float(talent.get("multiplier", 1)) if isinstance(talent, dict) else 1
        calibration = model.scale / talent_multiplier if talent_multiplier else model.scale
        if abs(calibration - 1) > 1e-12:
            parts.append(f"模型标定系数 × {calibration:.5g}")
    return "\n".join(parts)


def _numeric_formula_breakdown(
    model,
    loadout: Mapping[str, Any],
    reported_score: object,
) -> dict[str, Any]:
    factors = model.formula_factors
    stats = loadout.get("panel_stats")
    active_sets = loadout.get("set_flags")
    if not factors or not isinstance(stats, dict) or not isinstance(active_sets, list):
        return {
            "available": False,
            "message": "当前优化结果没有完整的面板或套装条件数据，无法可靠代入具体数值。",
        }

    calculated = evaluate_formula_breakdown(factors, stats, set(active_sets))
    score = float(calculated["unscaled_score"]) * model.scale
    try:
        expected_score = float(reported_score)
    except (TypeError, ValueError):
        expected_score = float("nan")
    if not isclose(score, expected_score, rel_tol=1e-10, abs_tol=1e-8):
        return {
            "available": False,
            "message": "当前可见输入与优化器记录分数无法核对，已隐藏代入算式。",
        }

    def components(terms, values, context: str | None = None, unit: str = ""):
        rows = []
        for term, value in zip(terms, values):
            kind = term.get("kind")
            source = "模型因子"
            if kind == "stat":
                source = "本次优化配装的最终面板（角色、武器、圣遗物及静态套装属性）"
            elif kind in {"constant", "constant_percent"}:
                source = term.get("source_label") or (
                    "模型设定的固定伤害值" if kind == "constant" else "模型已建模的固定加成"
                )
                if not isinstance(source, str) or _INTERNAL_FORMULA_TOKEN.search(source):
                    source = "模型已建模的固定加成"
            elif isinstance(kind, str) and kind.startswith("set_"):
                set_label = _formula_set_label(term)
                try:
                    flag = f"SET_{int(term['set_id'])}_{int(term['pieces'])}"
                except (KeyError, TypeError, ValueError):
                    flag = ""
                source = (
                    f"圣遗物套装效果：{set_label}"
                    if flag and flag in active_sets
                    else f"{set_label}条件未满足，本次贡献为 0"
                )
            rows.append({
                "label": _formula_term_label(term, context),
                "value": float(value),
                "unit": unit,
                "source": source,
            })
        return rows

    scaling_terms = list(factors.get("scaling_terms", []))
    damage_terms = list(factors.get("damage_terms", []))
    crit_rate_terms = list(factors.get("crit_rate_terms", []))
    crit_dmg_terms = list(factors.get("crit_dmg_terms", []))
    talent = factors.get("talent")
    talent_multiplier = (
        float(talent.get("multiplier", 1))
        if isinstance(talent, dict) else 1.0
    )
    calibration = model.scale / talent_multiplier if talent_multiplier else model.scale

    rows = [{
        "label": "伤害基数",
        "value": float(calculated["scaling"]),
        "unit": "基数点",
        "source": "本次优化配装的实际角色面板",
        "components": components(
            scaling_terms, calculated["scaling_term_values"], unit="基数点",
        ),
    }]
    if isinstance(talent, dict):
        talent_label = _TALENT_LABELS.get(talent.get("slot"), "角色技能")
        rows.append({
            "label": f"{talent_label}倍率",
            "value": talent_multiplier,
            "unit": "倍",
            "source": (
                f"本次请求的{talent_label}天赋等级 {talent.get('level')}；"
                f"以模型标定等级 {talent.get('baseline_level')} 为基准"
            ),
        })

    damage_component_rows = components(
        damage_terms, calculated["damage_term_values"], "damage", "%",
    )
    damage_total = float(calculated["damage_bonus"])
    rows.append({
        "label": "伤害加成系数",
        "value": float(calculated["damage_multiplier"]),
        "unit": "倍",
        "equation": {
            "kind": "percent_sum",
            "values": [float(item["value"]) for item in damage_component_rows],
            "result": float(calculated["damage_multiplier"]),
        },
        "source": "角色面板、已建模角色/武器效果与实际激活的圣遗物套装效果",
        "components": damage_component_rows,
        "total_percent": damage_total,
    })

    if factors.get("crit_enabled", True):
        crit_rate_component_rows = components(
            crit_rate_terms, calculated["crit_rate_term_values"], "crit_rate", "%",
        )
        crit_dmg_component_rows = components(
            crit_dmg_terms, calculated["crit_dmg_term_values"], "crit_dmg", "%",
        )
        rows.append({
            "label": "暴击期望系数",
            "value": float(calculated["crit_multiplier"]),
            "unit": "倍",
            "equation": {
                "kind": "crit_expected",
                "crit_rate": float(calculated["crit_rate"]),
                "crit_rate_cap": 100.0,
                "crit_dmg": float(calculated["crit_dmg"]),
                "result": float(calculated["crit_multiplier"]),
            },
            "source": "实际面板暴击率、暴击伤害及本次激活的套装暴击效果",
            "components": crit_rate_component_rows + crit_dmg_component_rows,
        })
    else:
        rows.append({
            "label": "暴击处理",
            "value": None,
            "unit": "",
            "source": "该评分模型明确关闭暴击计算",
            "components": [],
        })

    fixed_values = list(calculated["fixed_multiplier_values"])
    for value in fixed_values:
        rows.append({
            "label": "角色固有机制系数",
            "value": value,
            "unit": "倍",
            "source": "本次角色伤害模型中的固有机制输入",
        })

    if abs(calibration - 1) > 1e-12:
        rows.append({
            "label": "模型标定系数",
            "value": calibration,
            "unit": "倍",
            "source": "角色伤害模型标定值（包含模型设定的敌人防御/抗性基准）",
        })

    multipliers = [float(calculated["scaling"])]
    if isinstance(talent, dict):
        multipliers.append(talent_multiplier)
    multipliers.append(float(calculated["damage_multiplier"]))
    if calculated["crit_multiplier"] is not None:
        multipliers.append(float(calculated["crit_multiplier"]))
    multipliers.extend(fixed_values)
    if abs(calibration - 1) > 1e-12:
        multipliers.append(calibration)

    enemy = model.assumptions.get("enemy")
    if isinstance(enemy, dict):
        enemy_parts = []
        if isinstance(enemy.get("level"), int):
            enemy_parts.append(f"等级 {enemy['level']}")
        if isinstance(enemy.get("resistance"), (int, float)):
            enemy_parts.append(f"对应抗性 {float(enemy['resistance']):g}%")
        enemy_parts.append("防御/抗性按模型标定条件处理，不另行重复乘算")
        enemy_note = "敌人条件：" + "；".join(enemy_parts)
    else:
        enemy_note = "模型未提供敌人防御/抗性的独立输入，未虚构额外系数。"

    return {
        "available": True,
        "rows": rows,
        "multipliers": multipliers,
        "score": score,
        "reported_score": expected_score,
        "enemy_note": enemy_note,
    }


def _attack_label(value: object) -> str:
    if not isinstance(value, str):
        return "角色伤害动作"
    key = value.strip().casefold().split(maxsplit=1)[0].rstrip(":,;") if value.strip() else ""
    if key in _ATTACK_LABELS:
        return _ATTACK_LABELS[key]
    for prefix in sorted(_ATTACK_LABELS, key=len, reverse=True):
        if key.startswith(prefix + "_"):
            return _ATTACK_LABELS[prefix]
    return "角色伤害动作"


def _formula_display_label(model) -> str:
    label = model.label
    internal_attack = re.search(
        r"\b(?:normal|charged|plunge|plunging|skill|burst|rainDmg)"
        r"(?:_[a-z0-9_]+)?\b",
        label,
        re.IGNORECASE,
    )
    if not _INTERNAL_FORMULA_TOKEN.search(label) and not internal_attack:
        return label

    registry = available_models().get(model.key, {})
    character = registry.get("character_zh")
    if not isinstance(character, str) or not character.strip():
        character = "角色"
    attack = model.assumptions.get("attack") or model.assumptions.get("target")
    if not attack:
        attack = registry.get("attack")
    return f"{character} · {_attack_label(attack)}伤害公式"


def _formula_metadata_summary(model) -> list[str]:
    assumptions = model.assumptions
    summary = []
    calibration = assumptions.get("calibration")
    if isinstance(calibration, str) and "Genshin Optimizer" in calibration:
        version = re.search(r"Genshin Optimizer\s+([0-9.]+)", calibration)
        version_label = f" {version.group(1)}" if version else ""
        summary.append(f"来源：Genshin Optimizer{version_label} 本地标定数据")
    elif model.key == "neuvillette_charged_judgment":
        summary.append("来源：本地角色天赋数据与伤害模型")
    else:
        summary.append("来源：本地角色伤害模型目录")

    conditions = []
    if isinstance(assumptions.get("character_level"), int):
        conditions.append(f"角色等级 {assumptions['character_level']}")
    if isinstance(assumptions.get("ascension"), int):
        conditions.append(f"突破阶段 {assumptions['ascension']}")
    if isinstance(assumptions.get("constellation"), int):
        conditions.append(f"{assumptions['constellation']} 命")

    talents = assumptions.get("talents")
    if isinstance(talents, dict):
        talent_labels = [
            f"{_TALENT_LABELS.get(slot, '角色技能')} {level} 级"
            for slot, level in talents.items()
            if slot in _TALENT_LABELS and isinstance(level, int)
        ]
        if talent_labels:
            conditions.append("天赋：" + "、".join(talent_labels))

    attack = assumptions.get("attack") or assumptions.get("target")
    if attack:
        conditions.append(f"目标：{_attack_label(attack)}")
    element = assumptions.get("element")
    if isinstance(element, str) and element.upper() in _ELEMENT_LABELS:
        conditions.append(f"伤害类型：{_ELEMENT_LABELS[element.upper()]}")
    enemy = assumptions.get("enemy")
    if isinstance(enemy, dict):
        enemy_conditions = []
        if isinstance(enemy.get("level"), int):
            enemy_conditions.append(f"等级 {enemy['level']}")
        if isinstance(enemy.get("resistance"), (int, float)):
            enemy_conditions.append(f"对应抗性 {enemy['resistance']:g}%")
        if enemy.get("defense"):
            enemy_conditions.append("防御按模型校准条件处理")
        if enemy_conditions:
            conditions.append("敌人：" + "、".join(enemy_conditions))
    crit_mode = assumptions.get("crit_mode")
    if crit_mode in ("average", "noncrit"):
        conditions.append("暴击处理：" + ("期望暴击" if crit_mode == "average" else "不计暴击"))
    if conditions:
        summary.append("条件：" + "；".join(conditions))
    return summary


def _public_formula(model, selected: bool) -> dict[str, Any]:
    description = model.describe()
    assumptions = dict(description.get("assumptions") or {})
    sources = assumptions.pop("sources", [])
    expression = description.get("expression")
    display_expression = _readable_formula(model)
    return {
        "key": description["key"],
        "label": _formula_display_label(model),
        "expression": model.scaled_expression() if expression else None,
        "display_expression": display_expression,
        "scale": description.get("scale"),
        "formula_factors": description.get("formula_factors", {}),
        "external_inputs": description.get("formula_factors", {}).get("external_inputs", {}),
        "relevant_stats": description.get("relevant_stats", []),
        "assumptions": assumptions,
        "sources": sources if isinstance(sources, list) else [],
        "metadata_summary": _formula_metadata_summary(model),
        "selected": selected,
        "formula_type": "registered_model",
        "expression_note": (
            None if display_expression
            else "当前模型未提供结构化评分因子，因此不展示原始代码表达式。"
        ),
    }


@lru_cache(maxsize=128)
def _upstream_attack_references(
    character: str,
    character_label: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        catalog = list_damage_formulas(character)
    except ValueError as exc:
        return [], f"上游攻击公式索引不可用：{exc}"

    source = damage_engine_info().get("upstream")
    references = []
    for index, attack in enumerate(catalog.get("attacks", []), start=1):
        name = attack.get("name")
        if not isinstance(name, str) or not name:
            continue
        attack_type = attack.get("category") or name
        references.append({
            "key": f"upstream:{name}",
            "label": (
                f"{character_label or '角色'} · {_attack_label(attack_type)}参考节点 {index}"
            ),
            "expression": None,
            "scale": None,
            "assumptions": {},
            "sources": [source] if isinstance(source, str) and source else [],
            "metadata_summary": [
                "来源：上游角色伤害数据索引",
                "条件：此条目仅列出攻击节点信息，不参与本次配装评分",
            ],
            "selected": False,
            "formula_type": "attack_reference",
            "expression_note": (
                "上游接口只提供攻击节点类别与角色条件，不提供此节点的代数表达式；"
                "该参考节点不参与本次配装评分。"
            ),
        })
    return references, None


def _formula_models_for_member(member: dict[str, Any], request_member: dict[str, Any]) -> list[dict[str, Any]]:
    character = member.get("character")
    model_key = member.get("model")
    if not isinstance(character, str) or not isinstance(model_key, str):
        raise RuntimeError("优化结果缺少角色或伤害模型标识")

    character_config = request_member.get("character")
    if not isinstance(character_config, dict):
        character_config = {}
    weapon = request_member.get("weapon")
    if not isinstance(weapon, dict):
        weapon = {}
    options = dict(request_member.get("model_options") or {})
    common = {
        "level": int(request_member.get("level", character_config.get("level", 90))),
        "ascension": int(request_member.get("ascension", character_config.get("ascension", 6))),
        "constellation": int(request_member.get("constellation", 0)),
        "talents": request_member.get(
            "talents", {"auto": 10, "skill": 10, "burst": 10},
        ),
        "crit_mode": request_member.get("crit_mode", "average"),
        "weapon_level": int(weapon.get("level", 90)),
        "weapon_ascension": int(weapon.get("ascension", 6)),
        "refinement": int(weapon.get("refinement", 1)),
    }
    if weapon.get("id") is not None:
        common["weapon"] = weapon["id"]
    common.update(options)
    target_set = request_member.get(
        "fixed_four_piece_set",
        request_member.get("target_four_piece_set"),
    )
    try:
        catalog_spec(model_key)
    except ValueError:
        pass
    else:
        if target_set is not None:
            common["target_four_piece_set"] = int(target_set)
    selected_model = get_damage_model(character, model_key, **common)

    formulas = []
    found_selected = False
    loadout = member.get("loadout") or {}
    for key, metadata in available_models().items():
        if str(metadata.get("character", "")).casefold() != character.casefold():
            continue
        is_selected = key == model_key
        model = selected_model if is_selected else get_damage_model(character, key)
        formula = _public_formula(model, is_selected)
        if is_selected:
            formula["actual_inputs"] = {
                "talents": model.assumptions.get("talents", {}),
                "final_panel": loadout.get("final_panel", {}),
                "active_set_bonuses": loadout.get("active_set_bonuses", []),
            }
            formula["numeric_breakdown"] = _numeric_formula_breakdown(
                model, loadout, member.get("damage", loadout.get("damage")),
            )
        formulas.append(formula)
        found_selected = found_selected or is_selected
    if not found_selected:
        formula = _public_formula(selected_model, True)
        formula["actual_inputs"] = {
            "talents": selected_model.assumptions.get("talents", {}),
            "final_panel": loadout.get("final_panel", {}),
            "active_set_bonuses": loadout.get("active_set_bonuses", []),
        }
        formula["numeric_breakdown"] = _numeric_formula_breakdown(
            selected_model, loadout, member.get("damage", loadout.get("damage")),
        )
        formulas.append(formula)
    references, _ = _upstream_attack_references(character, member.get("name"))
    formulas.extend(references)
    formulas.sort(key=lambda formula: (not formula["selected"], formula["label"].casefold()))
    return formulas


def _public_result(
    result: dict[str, Any],
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_members = {
        row.get("character"): row
        for row in (request or {}).get("members", [])
        if isinstance(row, dict) and isinstance(row.get("character"), str)
    }
    members = []
    for member in result.get("members", []):
        public_member = _public_member(member)
        request_member = request_members.get(member.get("character"))
        if request_member is not None:
            public_member["formula_models"] = _formula_models_for_member(member, request_member)
            public_member["formula_semantics"] = (
                "本次优化只使用标记为“本次评分模型”的单个公式；同角色其他条目是可查阅的参考模型，"
                "不会与之联合评分。"
            )
            public_member["formula_reference_note"] = _upstream_attack_references(
                member["character"], member.get("name"),
            )[1]
        members.append(public_member)
    return {
        "members": members,
        "total_objective": result.get("total_objective"),
        "normalize": result.get("normalize"),
        "search": result.get("search", {}),
        "unused_count": len(result.get("unused", [])),
        "warnings": result.get("warnings", []),
    }


def _detail_item(
    kind: str,
    item_id: str | int,
    title: str,
    summary: str,
    fields: list[tuple[str, object]],
    *,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": str(item_id),
        "title": title,
        "summary": summary,
        "fields": [
            {"label": label, "value": value}
            for label, value in fields
            if value is not None and value != ""
        ],
        "notes": notes or [],
    }


def _artifact_detail(item: dict[str, Any]) -> dict[str, Any]:
    state = item["state"]
    equipped = item.get("equipped")
    return _detail_item(
        "artifacts",
        item["id"],
        item["piece_name"],
        f'{item["set_name"]} · +{state["level"]} · '
        f'装备角色：{equipped or "未装备"}',
        [
            ("圣遗物 ID", item["id"]),
            ("套装", item["set_name"]),
            ("部位", state["slot"]),
            ("星级", item["rarity"]),
            ("等级", state["level"]),
            ("初始词条数", state["initial_count"]),
            ("主词条", {"stat": state["main_stat"], "value": item["main_value"]}),
            ("副词条", item["active_substats"]),
            ("当前装备角色", equipped or "未装备"),
            ("评分", item.get("score")),
        ],
    )


def _projection_comparison(current: dict[str, Any], projected: dict[str, Any]) -> dict[str, Any]:
    current_by_character = {
        member.get("character"): member for member in current.get("members", [])
    }
    members = []
    weighted_relative_current = 0.0
    weighted_relative_projected = 0.0
    for member in projected.get("members", []):
        baseline = current_by_character.get(member.get("character"), {})
        baseline_pieces = {
            piece.get("slot"): piece
            for piece in baseline.get("loadout", {}).get("pieces", [])
        }
        candidates = []
        for piece in member.get("loadout", {}).get("pieces", []):
            expected = piece.get("expected_full_level")
            if expected is None:
                continue
            current_piece = baseline_pieces.get(piece.get("slot"))
            state = piece.get("state") or {}
            candidates.append({
                "slot": piece.get("slot"),
                "artifact_id": piece.get("id"),
                "piece_name": piece.get("piece_name"),
                "set_name": piece.get("set_name"),
                "main_stat": piece.get("main_stat"),
                "level": state.get("level"),
                "current_best_artifact_id": current_piece.get("id") if current_piece else None,
                "current_best_piece_name": (
                    current_piece.get("piece_name") if current_piece else None
                ),
                "replaces_current_best": (
                    current_piece is None or current_piece.get("id") != piece.get("id")
                ),
                "expected_full_level": expected,
            })
        current_damage = baseline.get("loadout", {}).get("damage")
        projected_damage = member.get("loadout", {}).get("damage")
        damage_delta = (
            projected_damage - current_damage
            if isinstance(projected_damage, (int, float))
            and isinstance(current_damage, (int, float))
            else None
        )
        weight = member.get("weight", 1.0)
        if isinstance(current_damage, (int, float)) and current_damage > 0 and isinstance(
            projected_damage, (int, float),
        ):
            weighted_relative_current += weight
            weighted_relative_projected += weight * projected_damage / current_damage
        members.append({
            "name": member.get("name"),
            "character": member.get("character"),
            "weight": weight,
            "current_damage": current_damage,
            "projected_damage": projected_damage,
            "damage_delta": damage_delta,
            "damage_delta_pct": (
                damage_delta / current_damage * 100
                if damage_delta is not None and current_damage > 0 else None
            ),
            "candidates": candidates,
        })
    objective_delta = (
        weighted_relative_projected - weighted_relative_current
        if weighted_relative_current else None
    )
    return {
        "current_objective": weighted_relative_current or None,
        "projected_objective": weighted_relative_projected or None,
        "objective_delta": objective_delta,
        "weighted_damage_delta_pct": (
            objective_delta / weighted_relative_current * 100
            if objective_delta is not None else None
        ),
        "members": members,
        "semantics": (
            "将剩余强化事件的期望副词条值累加到当前属性（3词条件包含+4解锁词条的期望），"
            "再把期望属性代入同一伤害模型重新配装；目标变化为各角色预期/当前伤害比按角色权重汇总。"
            "这是期望属性估算，不是实际强化保证，"
            "也不是对随机伤害结果分布的精确积分。"
        ),
    }


def _public_plan(result: dict[str, Any]) -> dict[str, Any]:
    domains = []
    for domain in result.get("domains", []):
        domains.append(
            {
                "domain_id": domain.get("domain_id"),
                "domain_name": domain.get("domain_name"),
                "drop_sets": domain.get("drop_sets", []),
                "score_mode": domain.get("score_mode"),
                "score_pct_per_100_resin": domain.get("score_pct_per_100_resin"),
                "weighted_expected_gain_pct": domain.get("weighted_expected_gain_pct"),
                "probability_any_member_upgrade": domain.get("probability_any_member_upgrade"),
                "expected_resin_to_any_upgrade": domain.get("expected_resin_to_any_upgrade"),
                "members": [
                    {
                        "name": member.get("name"),
                        "expected_gain_pct": member.get("expected_gain_pct"),
                        "expected_resin": member.get("expected_resin"),
                        "resin_for_50pct": member.get("resin_for_50pct"),
                        "resin_for_90pct": member.get("resin_for_90pct"),
                        "best_slot": member.get("best_slot"),
                    }
                    for member in domain.get("members", [])
                ],
            }
        )
    return {
        "domains": domains,
        "recommended_domain_id": result.get("recommended_domain_id"),
        "warnings": result.get("warnings", []),
    }


def _loadout_ids(result: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    return {
        member.get("character", ""): tuple(
            sorted(piece.get("id", "") for piece in (member.get("loadout") or {}).get("pieces", []))
        )
        for member in result.get("members", [])
    }


class AcceptanceService:
    def __init__(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="genshin-acceptance-")
        self._root = Path(self._temporary_directory.name)
        self._lock = threading.RLock()
        self._session: dict[str, Any] | None = None

    def close(self) -> None:
        self._temporary_directory.cleanup()

    def details(
        self,
        session_id: object,
        kind: object,
        query: object = "",
        page: object = 1,
        item_id: object = None,
    ) -> dict[str, Any]:
        if kind not in ("characters", "weapons", "artifacts"):
            raise ValueError("详情类别必须是 characters、weapons 或 artifacts")
        if not isinstance(query, str) or len(query) > 100:
            raise ValueError("搜索内容最多 100 个字符")
        if type(page) is not int or not 1 <= page <= 100_000:
            raise ValueError("页码必须是 1 到 100000 之间的整数")

        with self._lock:
            session = self._require_session(session_id)
            if item_id is not None:
                if kind != "artifacts" or not isinstance(item_id, str) or not item_id or len(item_id) > 80:
                    raise ValueError("详情标识无效")
                item = Inventory(session["account_dir"] / "account.sqlite3").get(item_id)
                return {"item": _artifact_detail(item)}

            account = session["account"]
            entries: list[tuple[dict[str, Any], str]] = []
            if kind == "characters":
                for character in account.get("roster", []):
                    record = get_game_record("characters", character["character"], language="zh")
                    profile_query = character.get("appearance") if character.get("identity") == "manekin" else character["character"]
                    character_profile = get_character_profile(profile_query or character["character"], language="zh")
                    talents = character.get("talents") or {}
                    ability_details = []
                    constellation_details = []
                    if character.get("identity") == "traveler":
                        talent_record = next(iter(character_profile["talents"]), {})
                        constellation_record = next(iter(character_profile["constellations"]), {})
                        for key in ("combat1", "combat2", "combat3", "passive1", "passive2", "passive3"):
                            ability = talent_record.get(key)
                            if isinstance(ability, dict) and ability.get("name"):
                                ability_details.append(
                                    f'{ability["name"]}：{ability.get("description", "")}'.rstrip("："),
                                )
                        for index in range(1, 7):
                            constellation = constellation_record.get(f"c{index}")
                            if isinstance(constellation, dict) and constellation.get("name"):
                                constellation_details.append(
                                    f'{constellation["name"]}：{constellation.get("description", "")}'.rstrip("："),
                                )
                    elif character.get("identity") == "manekin":
                        ability_details = [
                            f'{ability["name"]}：{ability["summary"]}'
                            for ability in character_profile["abilities"]
                        ]
                    appearance = (
                        get_game_record("characters", character["appearance"], language="zh")
                        if character.get("identity") == "traveler" else None
                    )
                    current_element = character.get("current_element")
                    fields = [
                        ("角色 ID", character.get("character_id", record.get("id"))),
                        ("英文名", character["character"]),
                        ("稀有度", character.get("rarity", record.get("rarity"))),
                        ("元素", (
                        f'Adaptive（当前：{character.get("current_element_name") or current_element}）'
                            if character.get("identity") == "manekin" and current_element
                            else record.get("elementText") or character.get("element")
                        )),
                        ("武器类型", record.get("weaponText") or character.get("weapon_type")),
                        ("等级", character.get("level")),
                        ("命座等级", character.get("constellation")),
                        ("天赋等级", talents),
                        ("装备武器", character.get("weapon")),
                        ("武器精炼", character.get("weapon_refinement")),
                        ("突破成长属性", record.get("substatText")),
                        ("所属地区", record.get("regionName") or record.get("region")),
                        ("所属组织", record.get("affiliation")),
                        ("生日", record.get("birthday")),
                        ("命之座名称", record.get("constellation")),
                        ("角色简介", record.get("description")),
                    ]
                    notes = (
                        [] if character.get("identity") == "manekin"
                        else ["模拟账号未保存角色突破阶段，因此不推算当前等级面板属性。"]
                    )
                    if character.get("identity") == "traveler":
                        fields.extend([
                            ("逻辑身份", "旅行者（空与荧共用）"),
                            ("当前元素形态", record["name"]),
                            ("外观", appearance.get("name") if appearance else None),
                            ("外观记录 ID", appearance.get("id") if appearance else None),
                            ("当前形态天赋图鉴", ability_details),
                            ("当前形态命座图鉴", constellation_details),
                        ])
                        notes.append("旅行者只占一个账号身份；切换元素形态不会产生另一套配装。")
                    elif character.get("identity") == "manekin":
                        fields.extend([
                            ("逻辑身份", "奇偶（Manekin / Manekina 共用）"),
                            ("外观", character.get("appearance_name")),
                            ("元素模式", "Adaptive（跟随旅行者当前元素）"),
                            ("当前元素", character.get("current_element_name") or current_element),
                            ("旅行者当前形态", character.get("traveler_form_name")),
                            ("命座", "无"),
                            ("能力摘要（倍率未接入）", ability_details),
                            ("战斗模型状态", character_profile["battle_model_status"]),
                            ("突破材料 / 摩拉", "无需材料；0 摩拉"),
                        ])
                        notes.extend([
                            "奇偶只占一个角色与配装身份；男、女外观不可同时出现在同一队伍。",
                            "能力描述据游戏技能说明整理；当前离线数据无完整技能参数与倍率，未校准且不参与配装评分。",
                        ])
                    item = _detail_item(
                        kind,
                        character.get("character_id", record.get("id", 0)),
                        character.get("name_zh") or record.get("name") or character["character"],
                        f'{character["character"]} · {character.get("level", "—")}级 · '
                        f'{character.get("weapon") or "未记录武器"}',
                        fields,
                        notes=notes,
                    )
                    entries.append((
                        item,
                        " ".join((
                            character["character"],
                            character.get("appearance_name", ""),
                            character.get("weapon", ""),
                        )),
                    ))
            elif kind == "weapons":
                for index, weapon in enumerate(account.get("weapons", [])):
                    matching = [
                        row for row in list_game_records(
                            "weapons", language="en", search=weapon["name"],
                        )
                        if row["name"].casefold() == weapon["name"].casefold()
                        and row.get("weaponType") == weapon.get("weapon_type")
                    ]
                    if not matching:
                        raise ValueError(f'找不到武器图鉴记录：{weapon["name"]}')
                    references = [
                        get_game_record("weapons", row["key"], language="zh")
                        for row in matching
                    ]
                    record = references[0]

                    def common_reference_value(field: str) -> object:
                        values = [reference.get(field) for reference in references]
                        return values[0] if all(value == values[0] for value in values) else None

                    refinement = weapon.get("refinement")
                    owner = weapon.get("owner")
                    item = _detail_item(
                        kind,
                        f'{record["id"]}-{index}',
                        record.get("name") or weapon["name"],
                        f'{weapon.get("level", "—")}级 · R{refinement or "—"} · '
                        f'装备角色：{owner or "未装备"}',
                        [
                            ("图鉴记录 ID", ", ".join(str(reference["id"]) for reference in references)),
                            ("英文名", weapon["name"]),
                            ("稀有度", common_reference_value("rarity")),
                            ("武器类型", common_reference_value("weaponText") or weapon.get("weapon_type")),
                            ("等级", weapon.get("level")),
                            ("精炼等级", refinement),
                            ("基础攻击力（图鉴原始值）", common_reference_value("baseAtkValue")),
                            ("副属性", common_reference_value("mainStatText")),
                            ("副属性数据键", common_reference_value("mainStatType")),
                            ("特效名称", common_reference_value("effectName")),
                            (
                                "当前精炼效果",
                                common_reference_value(f"r{refinement}") if refinement else None,
                            ),
                            ("装备角色", owner or "未装备"),
                            ("武器简介", common_reference_value("description")),
                        ],
                        notes=[
                            "模拟账号未保存武器突破阶段；基础攻击力和副属性显示图鉴原始字段，不推算当前等级属性。",
                            *(
                                ["账号仅记录同名武器，未保存唯一图鉴 ID；只显示这些记录共同拥有的图鉴字段。"]
                                if len(references) > 1 else []
                            ),
                        ],
                    )
                    entries.append((item, weapon["name"]))
            else:
                inventory = Inventory(session["account_dir"] / "account.sqlite3")
                for raw_item in inventory.list(sort="part"):
                    item = _artifact_detail(raw_item)
                    entries.append((
                        item,
                        f'{SETS[raw_item["state"]["set_id"]]["name_en"]} '
                        f'{raw_item["set_name"]} {raw_item["piece_name"]} '
                        f'{raw_item["state"]["slot"]} {raw_item["state"]["main_stat"]} '
                        f'{raw_item["id"]} {raw_item.get("equipped") or ""}',
                    ))

            token = query.strip().casefold()
            filtered = [
                item for item, search_text in entries
                if not token
                or token in search_text.casefold()
                or token in item["title"].casefold()
                or token in item["summary"].casefold()
                or any(
                    token in str(field["value"]).casefold()
                    for field in item["fields"]
                )
            ]
            total = len(filtered)
            pages = max(1, (total + DETAIL_PAGE_SIZE - 1) // DETAIL_PAGE_SIZE)
            start = (page - 1) * DETAIL_PAGE_SIZE
            return {
                "kind": kind,
                "query": query,
                "page": page,
                "page_size": DETAIL_PAGE_SIZE,
                "total": total,
                "pages": pages,
                "items": filtered[start:start + DETAIL_PAGE_SIZE],
            }

    def generate(self, payload: object) -> dict[str, Any]:
        config = validate_generation(payload)
        run_id = uuid.uuid4().hex
        account_dir = self._root / run_id
        command = [
            sys.executable,
            str(ROOT / "tools" / "simulate_account.py"),
            "--seed",
            str(config["seed"]),
            "--characters",
            str(config["characters"]),
            "--invested",
            str(optimizer_roster_size(config["characters"], config["artifacts"])),
            "--artifacts",
            str(config["artifacts"]),
            "--max-nodes",
            "50000",
            "--restarts",
            "1",
            "--max-iterations",
            "1",
            "--skip-plan",
            "--output",
            str(account_dir),
        ]
        if config["traveler_element"] is not None:
            command.extend(["--traveler-element", config["traveler_element"]])
        _run(command)
        account = _read_json(account_dir / "account.json")
        party_request = _read_json(account_dir / "party-request.json")
        if not (account_dir / "account.sqlite3").is_file():
            raise RuntimeError("账号模拟器没有生成可用的 SQLite 库存")
        if not party_request.get("members"):
            raise RuntimeError("账号模拟器没有生成可优化的角色")
        roster = [
            {
                "character": item.get("character"),
                "name_zh": item.get("name_zh") or item.get("character"),
                "element": item.get("element"),
                "weapon_type": item.get("weapon_type"),
                "weapon": item.get("weapon"),
                "invested": item.get("invested", False),
                "identity": item.get("identity"),
                "form_key": item.get("form_key"),
                "appearance": item.get("appearance"),
                "current_element": item.get("current_element"),
                "current_element_name": item.get("current_element_name"),
                "traveler_form_name": item.get("traveler_form_name"),
                "battle_support": item.get("battle_support"),
            }
            for item in account.get("roster", [])
        ]
        optimizable_names = {
            member.get("character")
            for member in party_request.get("members", [])
            if isinstance(member, dict)
        }
        optimizable_roster = [
            {
                **item,
                "model_target_four_piece_set": next(
                    (
                        member.get("target_four_piece_set")
                        for member in party_request.get("members", [])
                        if isinstance(member, dict)
                        and member.get("character") == item["character"]
                    ),
                    None,
                ),
            }
            for item in roster if item["character"] in optimizable_names
        ]
        with self._lock:
            self._session = {
                "id": run_id,
                "account_dir": account_dir,
                "account": account,
                "party_request": party_request,
                "optimization": None,
            }
        meta = account.get("meta", {})
        return {
            "session_id": run_id,
            "config": config,
            "account": {
                "game_version": meta.get("game_version"),
                "character_count": len(roster),
                "character_limit": available_character_count(),
                "optimizable_character_count": len(optimizable_roster),
                "weapon_count": len(account.get("weapons", [])),
                "artifact_count": meta.get("artifact_count", config["artifacts"]),
                "artifact_level_counts": meta.get("level_counts", {}),
                "artifact_level_weights": meta.get("artifact_level_weights", {}),
                "roster": roster,
                "optimizable_roster": optimizable_roster,
                "available_fixed_four_piece_sets": [
                    {"id": set_id, "name": info["name"]}
                    for set_id, info in sorted(SETS.items())
                    if 5 in info["rarities"] and "4" in info["effects"]
                ],
                "domains": account.get("domains", []),
                "set_counts": meta.get("set_counts", {}),
            },
        }

    def _require_session(self, session_id: object) -> dict[str, Any]:
        if not isinstance(session_id, str) or self._session is None or session_id != self._session["id"]:
            raise ValueError("账号会话已失效，请重新生成模拟账号")
        return self._session

    def optimize(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是 JSON 对象")
        with self._lock:
            session = self._require_session(payload.get("session_id"))
            base_request = session["party_request"]
            request = build_selected_request(
                base_request,
                payload.get("selected_characters"),
                payload.get("mode"),
                payload.get("seed"),
                payload.get("max_nodes"),
                fixed_four_piece_sets=payload.get("fixed_four_piece_sets"),
            )
            operation_id = uuid.uuid4().hex
            request_path = session["account_dir"] / f"request-{operation_id}.json"
            result_path = session["account_dir"] / f"result-{operation_id}.json"
            projected_request_path = session["account_dir"] / f"projected-request-{operation_id}.json"
            projected_result_path = session["account_dir"] / f"projected-result-{operation_id}.json"
            request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

            def run_optimization(input_path: Path, output_path: Path) -> None:
                _run([
                    sys.executable,
                    "-m",
                    "artifact_sim",
                    "-o",
                    str(output_path),
                    "party",
                    "--db",
                    str(session["account_dir"] / "account.sqlite3"),
                    "optimize",
                    str(input_path),
                ])

            run_optimization(request_path, result_path)
            result = _read_json(result_path)
            projected_request = {**request, "expected_full_level": True}
            projected_request_path.write_text(
                json.dumps(projected_request, ensure_ascii=False), encoding="utf-8",
            )
            run_optimization(projected_request_path, projected_result_path)
            projected_result = _read_json(projected_result_path)
            session["optimization"] = {
                "id": operation_id,
                "request": request,
                "result": result,
            }
            public_result = _public_result(result, request)
            public_projected_result = _public_result(projected_result)
            return {
                "optimization_id": operation_id,
                "selected_characters": payload["selected_characters"],
                "result": public_result,
                "projection": {
                    "result": public_projected_result,
                    "comparison": _projection_comparison(result, projected_result),
                },
            }

    def plan(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是 JSON 对象")
        with self._lock:
            session = self._require_session(payload.get("session_id"))
            previous = session.get("optimization")
            if not isinstance(previous, dict) or payload.get("optimization_id") != previous["id"]:
                raise ValueError("请先完成当前角色选择的配装优化，再继续规划")
            previous_request = previous["request"]
            fixed_four_piece_sets = {
                member["character"]: member["fixed_four_piece_set"]
                for member in previous_request["members"]
                if "fixed_four_piece_set" in member
            }
            request = build_selected_request(
                session["party_request"],
                [member["character"] for member in previous_request["members"]],
                "single" if len(previous_request["members"]) == 1 else "multi",
                previous_request["seed"],
                previous_request["max_nodes"],
                payload.get("domains"),
                fixed_four_piece_sets,
            )
            operation_id = uuid.uuid4().hex
            request_path = session["account_dir"] / f"plan-request-{operation_id}.json"
            result_path = session["account_dir"] / f"plan-result-{operation_id}.json"
            request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            _run(
                [
                    sys.executable,
                    "-m",
                    "artifact_sim",
                    "-o",
                    str(result_path),
                    "party",
                    "--db",
                    str(session["account_dir"] / "account.sqlite3"),
                    "plan",
                    str(request_path),
                ]
            )
            result = _read_json(result_path)
            if _loadout_ids(result) != _loadout_ids(previous["result"]):
                raise RuntimeError("规划阶段的优化配装与上一步结果不一致，请重新运行优化后再试")
            return {
                "optimization_id": previous["id"],
                "result": _public_plan(result),
            }


class AcceptanceHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: AcceptanceService) -> None:
        self.service = service
        super().__init__(address, AcceptanceRequestHandler)


class AcceptanceRequestHandler(BaseHTTPRequestHandler):
    server: AcceptanceHTTPServer

    @staticmethod
    def _page_content() -> bytes:
        page = PAGE.read_text(encoding="utf-8")
        page = page.replace("__CHARACTER_LIMIT__", str(available_character_count()))
        form_options = "\n".join(
            f'<option value="{escape(form["element"], quote=True)}">'
            f'{escape(form["name_zh"])}</option>'
            for form in traveler_forms(language="zh")
        )
        page = page.replace("__TRAVELER_ELEMENT_OPTIONS__", form_options)
        return page.replace("__ARTIFACT_LIMIT__", str(MAX_ARTIFACT_INVENTORY)).encode("utf-8")

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        content = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self) -> object:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效") from exc
        if length < 0 or length > 65536:
            raise ValueError("请求内容超过 64 KiB")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求不是有效的 UTF-8 JSON") from exc

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        route = parsed.path
        if route == "/api/health":
            self._send_json(200, {"ok": True})
            return
        if route == "/api/details":
            try:
                parameters = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=6)

                def one(name: str, default: str | None = None) -> str | None:
                    values = parameters.get(name)
                    if values is None:
                        return default
                    if len(values) != 1:
                        raise ValueError(f"{name} 只能出现一次")
                    return values[0]

                page_text = one("page", "1")
                try:
                    page = int(page_text or "")
                except ValueError as exc:
                    raise ValueError("页码必须是整数") from exc
                result = self.server.service.details(
                    one("session_id"),
                    one("kind"),
                    one("q", ""),
                    page,
                    one("id") or None,
                )
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, result)
            return
        if route == "/":
            try:
                page = self._page_content()
            except OSError as exc:
                self._send_json(500, {"error": f"无法读取界面文件：{exc}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)
            return
        self._send_json(404, {"error": "未找到页面"})

    def do_HEAD(self) -> None:
        route = urlsplit(self.path).path
        if route == "/api/health":
            content = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if route == "/":
            try:
                page = self._page_content()
            except OSError:
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_response(404)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()

    def do_POST(self) -> None:
        route = urlsplit(self.path).path
        actions = {
            "/api/generate": self.server.service.generate,
            "/api/optimize": self.server.service.optimize,
            "/api/plan": self.server.service.plan,
        }
        action = actions.get(route)
        if action is None:
            self._send_json(404, {"error": "未找到接口"})
            return
        try:
            result = action(self._read_json())
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, result)


def main() -> None:
    parser = argparse.ArgumentParser(description="启动本地实验效果验收界面")
    parser.add_argument("--port", type=int, default=8765, help="本地监听端口，传 0 可自动分配")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port 必须介于 0 和 65535 之间")
    service = AcceptanceService()
    try:
        server = AcceptanceHTTPServer(("127.0.0.1", args.port), service)
    except OSError as exc:
        service.close()
        parser.error(f"无法监听本地端口 {args.port}：{exc}")
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"实验效果验收界面已启动：{url}")
    print("仅监听本机；按 Ctrl+C 停止。")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭验收界面…")
    finally:
        server.server_close()
        service.close()


if __name__ == "__main__":
    main()
