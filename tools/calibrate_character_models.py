"""Generate the character damage-model catalog from the local GO bridge.

Run from the ArtifactSimulator directory:

    python tools/calibrate_character_models.py

The script probes the pinned Genshin Optimizer bundle for every local
character, fits a single-hit scalar model around a level-90 / C0 / 10-10-10
scenario, probes the selected attack at each relevant talent level from 1–10,
validates the fit on random artifact-sized stat changes, and writes
``damage/character_models.json``.  It is a build-time tool; normal use of
the simulator only reads the generated JSON.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import re
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from damage.build_objective import character_weapon_panel  # noqa: E402
from damage.character_models import (  # noqa: E402
    build_expression, model_from_spec, resolve_set_effects,
)
from data.game_data import SETS  # noqa: E402
from data.references import traveler_forms  # noqa: E402


BRIDGE = ROOT / "damage" / "go_damage" / "bridge.mjs"
REFERENCE = ROOT / "data" / "reference_data"
OUTPUT = ROOT / "damage" / "character_models.json"

TALENTS = {"auto": 10, "skill": 10, "burst": 10}
BASE_LEVEL = 90
BASE_ASCENSION = 6
RESISTANCE = 0
COMMON_SOURCES = [
    "https://www.genshindamagecalculator.com/guides/damage-formula/",
    "https://github.com/frzyc/GenshinOptimizer",
]
BUILD_SOURCES = {
    "Arlecchino": ["https://game8.co/games/Genshin-Impact/archives/382103"],
    "Mavuika": ["https://game8.co/games/Genshin-Impact/archives/461999"],
    "Neuvillette": ["https://www.prydwen.gg/genshin-impact/characters/neuvillette"],
}

# Curated main damage node, scenario and recommended four-piece set for the
# benchmark roster.  Everything else is selected automatically.  Values for
# list conditions must match the upstream list entries.
PROFILES: dict[str, dict] = {
    "Arlecchino": {
        "attack": "normal_3", "conditions": {"bondPercent": "200"}, "target_set": 15035,
        "label": "阿蕾奇诺普通攻击第三段（生命之契满层）",
    },
    "HuTao": {
        "attack": "charged",
        "conditions": {"GuideToAfterlifeVoyage": True, "FlutterBy": True, "SanguineRouge": True},
        "target_set": 15019,
        "weapon": "StaffOfHoma",
        "weapon_name": "Staff of Homa",
        "panel_bonus_stats": {"HP_PERCENT": 20.0},
        "label": "胡桃重击（彼岸蝶舞、半血以下、精一护摩）",
    },
    "Furina": {
        "attack": "skill_chevalDmg", "conditions": {"skillHpConsumeStacks": 4},
        "target_set": 15032, "label": "芙宁娜沙龙成员·乌瑟勋爵",
    },
    "Nahida": {
        "attack": "karma_dmg", "conditions": {"partyInBurst": True, "a1ActiveInBurst": True},
        "target_set": 15026, "label": "纳西妲灭净三业",
    },
    "Alhaitham": {
        "attack": "normal_2_dendro",
        "conditions": {"withMirrors": True, "mirrorsConsumed": 3, "debateStacks": 4},
        "target_set": 15026, "label": "艾尔海森琢光镜协同攻击",
    },
    "RaidenShogun": {
        "attack": "burst_hit41",
        "conditions": {"InBurst": True, "burstResolve": "60", "skillEye": "skillEye"},
        "target_set": 15020, "label": "雷电将军梦想一心·四段",
    },
    "KamisatoAyaka": {
        "attack": "burst_cutting",
        "conditions": {"afterSprint": "afterSprint", "afterSkillA1": "afterSkill"},
        "target_set": 14001, "label": "神里绫华霜灭切割",
    },
    "Ganyu": {
        "attack": "charged_frostflakeBloom", "conditions": {"A1": True, "A4": True},
        "target_set": 15003, "label": "甘雨霜华矢·霜华绽发",
    },
    "Xiao": {
        "attack": "plunging_high_anemo",
        "conditions": {"inBurst": "inBurst", "a1BurstStack": "4", "a4SkillStack": 3, "offField": False},
        "target_set": 15023, "label": "魈靖妖傩舞·高空坠落",
    },
    "Wanderer": {
        "attack": "normal_0",
        "conditions": {"afterSkill": True, "skillPyroContact": "pyro", "skillCryoContact": "cryo"},
        "target_set": 15027, "label": "流浪者羽画·风姿华歌普攻",
    },
    "Yelan": {
        "attack": "burst_throw", "conditions": {"a4Stacks": 14},
        "target_set": 15020, "label": "夜兰渊图玲珑骰协同攻击",
    },
    "Navia": {
        "attack": "totalShardDmg",
        "conditions": {"a1AfterSkill": True, "shotsHit": 11, "skillCharges": 6},
        "target_set": 15034, "label": "娜维娅典仪式晶火",
    },
    "Clorinde": {
        "attack": "skill_normalDmg", "conditions": {"a1Reactions": 3, "a4BondChanges": 2},
        "target_set": 15035, "label": "克洛琳德夜巡·普攻",
    },
    "Mualani": {
        "attack": "skill_surgingDmg", "conditions": {"a4Stacks": "3"},
        "target_set": 15038, "label": "玛拉妮冲浪时间·爆发",
    },
    "Kinich": {
        "attack": "skill_cannon", "conditions": {"a4Stacks": 2},
        "target_set": 15038, "label": "基尼奇夜魂迸发·炮击",
    },
    "Mavuika": {
        "attack": "burst",
        "conditions": {"a1NsBurst": True, "burstSpirit": "200", "a4TimeSinceBurst": "0"},
        "target_set": 15038, "label": "玛薇卡燔天之时",
    },
    "Skirk": {
        "attack": "burst_finalDmg",
        "conditions": {"a4DeathStacks": 3, "burstSerpentOver": 22, "burstVoidAbsorb": "3"},
        "target_set": 15040, "label": "丝柯克极恶技·尽",
    },
    "Chasca": {
        "attack": "skill_shiningShellDmg_pyro", "conditions": {"a1InMultitarget": True},
        "target_set": 15038, "label": "恰斯卡灵枪·焕光",
    },
    "Emilie": {
        "attack": "skill_level2", "conditions": {"a4Burning": True},
        "target_set": 15036, "label": "艾梅莉埃柔灯之匣",
    },
    "Xilonen": {
        "attack": "burst_beat",
        "conditions": {"nsBlessing": True, "nsBurst": True, "sourceActive": True},
        "target_set": 15021, "label": "希诺宁音火锻淬",
    },
    "Bennett": {
        "attack": "burst", "conditions": {"activeInArea": "activeInArea"},
        "target_set": 15007, "label": "班尼特美妙旅程",
    },
    "Xiangling": {
        "attack": "burst_dmgNado", "conditions": {"afterPyronado": True, "afterGuobaHit": True},
        "target_set": 15020, "label": "香菱旋火轮",
    },
    "Xingqiu": {
        "attack": "burst", "conditions": {"burst": True, "skill": True},
        "target_set": 15020, "label": "行秋古华剑·裁雨留虹",
    },
    "YaeMiko": {
        "attack": "skill_3", "target_set": 15032, "label": "八重神子杀生樱",
    },
    "Fischl": {
        "attack": "skill_oz", "target_set": 15032, "label": "菲谢尔奥兹",
    },
    "Keqing": {
        "attack": "charged_1_electro",
        "conditions": {"afterBurst": "afterBurst", "afterReact": "afterReact"},
        "target_set": 15019, "label": "刻晴重击",
    },
    "Lyney": {
        "attack": "charged_spiritbreath",
        "conditions": {"propStacks": 5, "a4AffectedByPyro": True, "a1DrainHp": True},
        "target_set": 15003, "label": "林尼隐具魔术箭·灵息之刺",
    },
    "Wriothesley": {
        "attack": "skill_enhanced_2", "conditions": {"a4EdictStacks": 5},
        "target_set": 15031, "label": "莱欧斯利冰牙突驰·普攻",
    },
    "Nilou": {
        "attack": "skill_whirl1", "conditions": {"a1AfterSkill": True, "a1AfterHit": True},
        "target_set": 15029, "label": "妮露七域舞步",
    },
    "Cyno": {
        "attack": "burst_normal_2", "conditions": {"afterBurst": True, "a1Judication": True},
        "target_set": 15026, "label": "赛诺启途誓使·普攻",
    },
    "Tighnari": {
        "attack": "charged_wreath", "conditions": {"p1AfterWreath": True},
        "target_set": 15003, "label": "提纳里藏蕴花矢",
    },
    "Yoimiya": {
        "attack": "normal_2_pyro", "conditions": {"skill": "skill", "a1": 10},
        "target_set": 15019, "label": "宵宫焰硝庭火舞·普攻",
    },
    "Eula": {
        "attack": "burst_lightfall",
        "conditions": {"LightfallSword": 30, "Grimheart": "stack2", "grimheartConsumed": True},
        "target_set": 15018, "label": "优菈凝浪之光剑·光降之剑",
    },
    "Noelle": {
        "attack": "charged_final", "conditions": {"SweepingTime": True},
        "target_set": 15021, "label": "诺艾尔大扫除·终结",
    },
    "Albedo": {
        "attack": "skill_blossom", "conditions": {"skillInField": "skillInField"},
        "target_set": 15021, "label": "阿贝多阳华·刹那之花",
    },
    "Shenhe": {
        "attack": "burst",
        "conditions": {"asc1": "field", "asc4": "press", "burst": "burst", "quill": "quill"},
        "target_set": 15007, "label": "申鹤神女遣灵真诀",
    },
    "Citlali": {
        "attack": "frostfallStormDmg", "conditions": {"a1NsFreezeMelt": True},
        "target_set": 15038, "label": "茜特菈莉霜陨风暴",
    },
    "Varesa": {
        "attack": "burst_volcanoDmg", "conditions": {"a4NsBurst": 2, "a1Rainbow": True},
        "target_set": 15038, "label": "瓦雷莎闪烈降临",
    },
    "Nefer": {
        "attack": "skill_nefer2Dmg",
        "conditions": {"a1VeilStacks": 5, "burstVeilsAbsorbed": 5},
        "target_set": 15041, "label": "奈芙尔幻影·二段",
    },
    "Flins": {
        "attack": "burst_finalLunarDmg", "target_set": 15020, "label": "弗林斯终末月感电",
    },
    "Lauma": {
        "attack": "skill_frostgroveDmg",
        "conditions": {"a1AfterSkill": True, "skillVerdantDew": 3},
        "target_set": 15041, "label": "菈乌玛霜林",
    },
    "Aino": {
        "attack": "burst", "target_set": 15020, "label": "爱诺精密水冷仪",
    },
}


def _load_gzip(name: str) -> dict:
    with gzip.open(REFERENCE / name, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _go_key(name_en: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", name_en)


def _call(node: str, action: str, payload: dict) -> dict:
    process = subprocess.run(
        [node, str(BRIDGE)],
        input=json.dumps({"action": action, "payload": payload}, ensure_ascii=False),
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or f"exit {process.returncode}")
    return json.loads(process.stdout)


def _calculate(node: str, go_key: str, attack: str, *, mode: str,
               conditions: dict | None = None, bonus_stats: dict | None = None,
               weapon: str | None = None, talents: dict | None = None) -> dict:
    payload = {
        "character": go_key,
        "level": BASE_LEVEL,
        "ascension": BASE_ASCENSION,
        "constellation": 0,
        "talents": talents or TALENTS,
        "conditions": conditions or {},
        "bonus_stats": bonus_stats or {},
        "enemy": {"level": 90, "resistance": RESISTANCE},
        "mode": mode,
        "attacks": [attack],
    }
    if weapon is not None:
        payload["weapon"] = {
            "key": weapon, "level": BASE_LEVEL, "ascension": BASE_ASCENSION, "refinement": 1,
        }
    return _call(node, "calculate", payload)


def _category(attack: dict) -> str:
    name = str(attack["name"]).casefold()
    category = str(attack.get("category") or "auto").casefold()
    if "plunging" in name or "plunge" in name:
        return "plunge"
    if "charged" in name:
        return "charged"
    if category == "skill":
        return "skill"
    if category == "burst":
        return "burst"
    return "normal"


def _attack_family(attack: dict) -> str:
    return _category(attack)


def _talent_scaling_curve(
    node: str,
    go_key: str,
    attack: str,
    category: str,
    conditions: dict,
    weapon: str | None,
    baseline_damage: float,
) -> dict:
    slot = "auto" if category in ("normal", "charged", "plunge") else category
    multipliers = {}
    for level in range(1, 11):
        if level == TALENTS[slot]:
            damage = baseline_damage
        else:
            talents = dict(TALENTS)
            talents[slot] = level
            result = _calculate(
                node,
                go_key,
                attack,
                mode="noncrit",
                conditions=conditions,
                weapon=weapon,
                talents=talents,
            )
            damage = float(result["attacks"][0]["damage"])
        if not math.isfinite(damage) or damage <= 0 or baseline_damage <= 0:
            raise ValueError(f"{slot} Lv.{level} 的上游标定倍率无效")
        multipliers[str(level)] = _round(damage / baseline_damage)
    multipliers[str(TALENTS[slot])] = 1.0
    return {
        "slot": slot,
        "baseline_level": TALENTS[slot],
        "multipliers": multipliers,
        "source": "Genshin Optimizer 10.40.0 bridge；固定其余条件与面板，仅改变对应天赋等级",
    }


def _auto_conditions(conditions: dict) -> dict:
    result = {}
    for name, rule in conditions.items():
        low = name.casefold()
        if re.match(r"^c[1-6]", low) or low.startswith("lock"):
            continue
        if rule.get("type") == "bool":
            result[name] = True
    return result


def _candidate_attacks(attacks: list[dict]) -> list[dict]:
    result = []
    for attack in attacks:
        name = str(attack["name"]).casefold()
        if (
            re.match(r"^c[1-6]", name)
            or name.startswith(("lock_", "a1", "a4", "passive", "lockedpassive", "constellation"))
        ):
            continue
        result.append(attack)
    return result


def _choose_attack(node: str, go_key: str, attacks: list[dict], conditions: dict,
                   weapon: str | None = None) -> dict:
    candidates = _candidate_attacks(attacks)
    non_plunge = [attack for attack in candidates if _category(attack) != "plunge"]
    if non_plunge:
        candidates = non_plunge
    if not candidates:
        raise RuntimeError("没有可用的伤害节点")
    names = [attack["name"] for attack in candidates]
    payload = {
        "character": go_key, "level": BASE_LEVEL, "ascension": BASE_ASCENSION,
        "constellation": 0, "talents": TALENTS, "conditions": conditions,
        "enemy": {"level": 90, "resistance": RESISTANCE}, "mode": "noncrit",
        "attacks": names,
    }
    if weapon is not None:
        payload["weapon"] = {
            "key": weapon, "level": BASE_LEVEL, "ascension": BASE_ASCENSION, "refinement": 1,
        }
    data = _call(node, "calculate", payload)
    damage = {row["name"]: float(row["damage"]) for row in data["attacks"]}
    best = None
    best_score = -math.inf
    for attack in candidates:
        category = _category(attack)
        weight = 0.9 if category == "plunge" else 1.0
        score = damage.get(attack["name"], 0.0) * weight
        if score > best_score:
            best, best_score = attack, score
    if best is None or best_score <= 0:
        raise RuntimeError("所有伤害节点的基准伤害均为零")
    return best


def _choose_target_set(element: str, category: str, region: str | None) -> int | None:
    if region and "纳塔" in region:
        return 15038
    preferences = {
        "CRYO": {"normal": 14001, "charged": 14001},
        "HYDRO": {"normal": 15016, "charged": 15016},
        "ANEMO": {"normal": 15027, "charged": 15027},
        "GEO": {"normal": 15034, "charged": 15034},
        "DENDRO": {"normal": 15026, "charged": 15026, "skill": 15026, "burst": 15026},
        "PYRO": {"charged": 15019, "normal": 15019},
        "ELECTRO": {"charged": 15019, "normal": 15001},
    }
    if element in preferences and category in preferences[element]:
        return preferences[element][category]
    by_category = {
        "skill": [15032, 15038, 15036],
        "burst": [15020, 15007, 15040, 15036],
        "charged": [15031, 15019, 15003, 15027],
        "normal": [15031, 15019, 15001, 15040],
        "plunge": [15039, 15019, 15027],
    }
    for set_id in by_category.get(category, [15031, 15036, 15038]):
        info = SETS.get(set_id)
        if info and 5 in info["rarities"] and "4" in info["effects"]:
            return set_id
    return None


def _round(value: float) -> float:
    return float(f"{value:.12g}")


def _local_panel(name_en: str, weapon: str | None = None, bonus_stats: dict | None = None) -> dict:
    stats, _ = character_weapon_panel(
        {"id": name_en, "level": BASE_LEVEL, "ascension": BASE_ASCENSION},
        None if weapon is None else {"id": weapon, "level": BASE_LEVEL, "ascension": BASE_ASCENSION},
        bonus_stats,
    )
    return {key: float(value) for key, value in stats.items()}


def _validation_samples(rng: random.Random, element: str, count: int = 12) -> list[dict]:
    samples = []
    for _ in range(count):
        sample = {
            "hp_": rng.uniform(0.0, 0.35),
            "atk_": rng.uniform(0.0, 0.55),
            "def_": rng.uniform(0.0, 0.35),
            "eleMas": rng.uniform(0.0, 220.0),
            "enerRech_": rng.uniform(0.0, 0.4),
            "critRate_": rng.uniform(0.0, 0.55),
            "critDMG_": rng.uniform(0.0, 1.1),
        }
        key = "physical_dmg_" if element in ("PHYSICAL", "NONE") else f"{element.casefold()}_dmg_"
        sample[key] = rng.uniform(0.0, 0.55)
        samples.append(sample)
    return samples


def _model_stats(local: dict, sample: dict, element: str) -> dict:
    stats = {key: Decimal(str(value)) for key, value in local.items()}

    def add(key: str, value: float) -> None:
        stats[key] = stats.get(key, Decimal(0)) + Decimal(str(value))

    add("HP_PERCENT", sample["hp_"] * 100)
    add("ATK_PERCENT", sample["atk_"] * 100)
    add("DEF_PERCENT", sample["def_"] * 100)
    add("ELEMENTAL_MASTERY", sample["eleMas"])
    add("ENERGY_RECHARGE", sample["enerRech_"] * 100)
    add("CRIT_RATE", sample["critRate_"] * 100)
    add("CRIT_DMG", sample["critDMG_"] * 100)
    if element in ("PHYSICAL", "NONE"):
        add("PHYSICAL_DMG_BONUS", sample["physical_dmg_"] * 100)
    else:
        stat = f"{element}_DMG_BONUS"
        add(stat, sample[f"{element.casefold()}_dmg_"] * 100)
    return stats


def calibrate_one(
    node: str,
    character: dict,
    *,
    seed: int = 0,
    selected_attack: str | None = None,
    is_default: bool = True,
) -> dict | None:
    go_key = _go_key(character["name_en"])
    profile = PROFILES.get(go_key, {})
    weapon = profile.get("weapon")
    weapon_name = profile.get("weapon_name")
    panel_bonus = dict(profile.get("panel_bonus_stats") or {})
    try:
        catalog = _call(node, "formulas", {"character": go_key})
    except Exception as exc:  # noqa: BLE001 - report and skip unsupported characters
        print(f"[skip] {character['name_en']} ({go_key}): {exc}", file=sys.stderr)
        return None

    conditions = dict(profile.get("conditions") or {})
    if not conditions:
        conditions = _auto_conditions(catalog.get("conditions") or {})
    try:
        requested_attack = selected_attack or profile.get("attack")
        attack = next(
            (row for row in catalog["attacks"] if row["name"] == requested_attack),
            None,
        )
        if attack is None:
            attack = _choose_attack(node, go_key, catalog["attacks"], conditions, weapon=weapon)
    except Exception as exc:  # noqa: BLE001
        print(f"[skip] {character['name_en']} attack: {exc}", file=sys.stderr)
        return None

    attack_name = attack["name"]
    category = _category(attack)
    try:
        baseline = _calculate(node, go_key, attack_name, mode="noncrit", conditions=conditions, weapon=weapon)
        average = _calculate(node, go_key, attack_name, mode="average", conditions=conditions, weapon=weapon)
    except Exception as exc:  # noqa: BLE001
        print(f"[skip] {character['name_en']} baseline: {exc}", file=sys.stderr)
        return None

    result = baseline["attacks"][0]
    element = str(result.get("element") or character.get("element") or "physical").upper()
    if element.startswith("ELEMENT_"):
        element = element.removeprefix("ELEMENT_")
    panel = baseline["panel"]
    local = _local_panel(character["name_en"], weapon_name, panel_bonus or None)
    base_damage = float(result["damage"])
    if not math.isfinite(base_damage) or base_damage <= 0:
        return None

    probes = {
        "hp": {"hp": 1000},
        "atk": {"atk": 1000},
        "def": {"def": 1000},
        "em": {"eleMas": 200},
        "er": {"enerRech_": 0.5},
    }
    element_key = "physical_dmg_" if element in ("PHYSICAL", "NONE") else f"{element.casefold()}_dmg_"
    probes["element_dmg"] = {element_key: 0.5}
    values = {}
    for label, bonus in probes.items():
        try:
            data = _calculate(
                node, go_key, attack_name, mode="noncrit", conditions=conditions,
                bonus_stats=bonus, weapon=weapon,
            )
            values[label] = float(data["attacks"][0]["damage"])
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {character['name_en']} probe {label}: {exc}", file=sys.stderr)
            values[label] = base_damage

    coefficients = {
        "HP": (values["hp"] - base_damage) / 1000,
        "ATK": (values["atk"] - base_damage) / 1000,
        "DEF": (values["def"] - base_damage) / 1000,
        "ELEMENTAL_MASTERY": (values["em"] - base_damage) / 200,
        "ENERGY_RECHARGE": (values["er"] - base_damage) / 50,
    }
    coefficients = {
        stat: _round(value) for stat, value in coefficients.items()
        if abs(value) > 1e-9
    }
    panel_scaling = {
        "HP": local.get("BASE_HP", 0.0) * (1 + local.get("HP_PERCENT", 0.0) / 100) + local.get("HP", 0.0),
        "ATK": local.get("BASE_ATK", 0.0) * (1 + local.get("ATK_PERCENT", 0.0) / 100) + local.get("ATK", 0.0),
        "DEF": local.get("BASE_DEF", 0.0) * (1 + local.get("DEF_PERCENT", 0.0) / 100) + local.get("DEF", 0.0),
        "ELEMENTAL_MASTERY": local.get("ELEMENTAL_MASTERY", 0.0),
        "ENERGY_RECHARGE": local.get("ENERGY_RECHARGE", 100.0),
    }
    scaling = sum(coefficients.get(stat, 0.0) * value for stat, value in panel_scaling.items())
    constant = base_damage - scaling
    if abs(constant) < 1e-6 * base_damage:
        constant = 0.0

    ratio = values["element_dmg"] / base_damage
    dmg_bonus = 0.0
    baseline_bonus = 0.0
    dmg_note = None
    if ratio > 1.000001:
        baseline_bonus = 50.0 / (ratio - 1.0) - 100.0
        # The local panel already includes the ascension elemental DMG stat.
        # The expression reads that stat directly, so the calibrated baseline
        # bonus must not contain it a second time.
        element_stat = "PHYSICAL_DMG_BONUS" if element in ("PHYSICAL", "NONE") else f"{element}_DMG_BONUS"
        dmg_bonus = baseline_bonus - float(local.get(element_stat, 0.0))
        if not math.isfinite(dmg_bonus) or dmg_bonus < -95 or dmg_bonus > 1000:
            baseline_bonus = 0.0
            dmg_bonus = 0.0
            dmg_note = "元素伤害加成探针无法稳定求解，按 0 处理"
    else:
        dmg_note = "伤害节点不响应元素伤害加成探针，按 0 处理"

    # The probes measure total damage, which already contains the baseline
    # DMG-bonus multiplier.  Normalize the scaling part so the expression can
    # add that multiplier back exactly once.  Use the full baseline multiplier
    # here; the expression's constant excludes the ascension stat but still
    # reads it from the panel.
    factor = 1.0 + baseline_bonus / 100.0
    if factor > 0 and abs(factor - 1.0) > 1e-12:
        coefficients = {stat: _round(value / factor) for stat, value in coefficients.items()}
        constant = _round(constant / factor)

    cr0 = float(local.get("CRIT_RATE", 5.0))
    cd0 = float(local.get("CRIT_DMG", 50.0))
    average_damage = float(average["attacks"][0]["damage"])
    crit_enabled = average_damage > base_damage * 1.000001
    kit_cr = 0.0
    kit_cd = 0.0
    if crit_enabled and cr0 > 0 and cd0 > 0:
        actual_ratio = average_damage / base_damage
        expected_ratio = 1.0 + min(1.0, cr0 / 100.0) * cd0 / 100.0
        if abs(actual_ratio - expected_ratio) > 1e-6:
            cd_eff = (actual_ratio - 1.0) / min(1.0, cr0 / 100.0) * 100.0
            cr_eff = (actual_ratio - 1.0) / (cd0 / 100.0) * 100.0
            candidates = [
                ("crit_dmg_bonus", cd_eff - cd0),
                ("crit_rate_bonus", cr_eff - cr0),
            ]
            plausible = [
                (name, value) for name, value in candidates
                if (name == "crit_rate_bonus" and -80.0 <= value <= 100.0)
                or (name == "crit_dmg_bonus" and -80.0 <= value <= 400.0)
            ]
            if plausible:
                name, value = min(plausible, key=lambda item: abs(item[1]))
                if name == "crit_rate_bonus":
                    kit_cr = value
                else:
                    kit_cd = value
            else:
                kit_cr = float(panel["critRate_"]) * 100.0 - cr0
                kit_cd = float(panel["critDMG_"]) * 100.0 - cd0
    if abs(kit_cr) < 1e-9:
        kit_cr = 0.0
    if abs(kit_cd) < 1e-9:
        kit_cd = 0.0
    if not coefficients and abs(constant) < 1e-9:
        return None

    target_set = profile.get("target_set") if is_default else None
    if target_set is None:
        target_set = _choose_target_set(element, category, character.get("region"))
    resolved = resolve_set_effects(target_set, category, element)
    set_effects = {str(target_set): resolved} if target_set is not None and resolved else {}
    if is_default and profile.get("label"):
        label = profile["label"]
    else:
        family_label = {
            "normal": "普攻",
            "skill": "元素战技",
            "burst": "元素爆发",
            "charged": "重击",
            "plunge": "下落攻击",
        }.get(category, "伤害")
        label = f"{character['name']}·{family_label}（{attack_name}）"
    spec = {
        "key": f"{go_key.casefold()}_{attack_name.casefold()}",
        "character": character["name_en"],
        "character_zh": character["name"],
        "go_key": go_key,
        "weapon_type": character.get("weapon_type"),
        "label": label,
        "is_default": is_default,
        "element": element,
        "attack": attack_name,
        "category": category,
        "target_four_piece_set": target_set,
        "coefficients": coefficients,
        "constant": _round(constant),
        "dmg_bonus": _round(dmg_bonus),
        "crit_rate_bonus": _round(kit_cr),
        "crit_dmg_bonus": _round(kit_cd),
        "crit_enabled": crit_enabled,
        "set_effects": set_effects,
        "panel_bonus_stats": panel_bonus,
        "weapon": weapon_name,
        "talent_scaling": _talent_scaling_curve(
            node, go_key, attack_name, category, conditions, weapon, base_damage,
        ),
        "assumptions": {
            "character_level": BASE_LEVEL,
            "ascension": BASE_ASCENSION,
            "constellation": 0,
            "talents": TALENTS,
            "conditions": conditions,
            "attack": attack_name,
            "category": category,
            "element": element,
            "enemy": {"level": 90, "resistance": RESISTANCE, "defense": "standard same-level"},
            "weapon": weapon_name,
            "weapon_refinement": 1 if weapon_name else None,
            "weapon_passive": (
                "仅覆盖标定武器；静态面板加成见 panel_bonus_stats"
                if weapon_name else "未纳入；由调用方提供武器白值与副属性"
            ),
            "scope": "单段伤害、无元素反应、无队伍增益",
            "crit_mode": "average",
        },
        "sources": COMMON_SOURCES + BUILD_SOURCES.get(go_key, []),
    }

    # Validate the linear fit on artifact-sized perturbations.
    model = model_from_spec(spec, "average")
    rng = random.Random(seed + len(go_key) * 17 + len(attack_name))
    errors = []
    for sample in _validation_samples(rng, element):
        try:
            expected = float(_calculate(
                node, go_key, attack_name, mode="average", conditions=conditions,
                bonus_stats=sample, weapon=weapon,
            )["attacks"][0]["damage"])
            predicted = float(model.evaluate(_model_stats(local, sample, element), set()))
        except Exception:  # noqa: BLE001
            continue
        if expected > 0:
            errors.append(abs(predicted - expected) / expected)
    max_error = max(errors) if errors else None
    if max_error is None:
        accuracy = "unvalidated"
    elif max_error <= 0.02:
        accuracy = "high"
    elif max_error <= 0.10:
        accuracy = "medium"
    else:
        accuracy = "low"
    spec["accuracy"] = accuracy
    spec["calibration"] = {
        "optimizer": "Genshin Optimizer 10.40.0 bridge",
        "baseline_panel": {key: float(value) for key, value in panel.items()},
        "baseline_noncrit_damage": base_damage,
        "baseline_average_damage": float(average["attacks"][0]["damage"]),
        "probe_damage": {key: float(value) for key, value in values.items()},
        "validation_samples": len(errors),
        "max_relative_error": max_error,
        "median_relative_error": statistics.median(errors) if errors else None,
        "accuracy": accuracy,
        "notes": [note for note in (dmg_note,) if note],
        "expression": build_expression(spec, "average"),
    }
    return spec


def _representative_attacks(
    node: str,
    go_key: str,
    attacks: list[dict],
    conditions: dict,
    weapon: str | None,
) -> dict[str, dict]:
    candidates = _candidate_attacks(attacks)
    if not candidates:
        return {}
    payload = {
        "character": go_key,
        "level": BASE_LEVEL,
        "ascension": BASE_ASCENSION,
        "constellation": 0,
        "talents": TALENTS,
        "conditions": conditions,
        "enemy": {"level": 90, "resistance": RESISTANCE},
        "mode": "noncrit",
        "attacks": [attack["name"] for attack in candidates],
    }
    if weapon is not None:
        payload["weapon"] = {
            "key": weapon,
            "level": BASE_LEVEL,
            "ascension": BASE_ASCENSION,
            "refinement": 1,
        }
    result = _call(node, "calculate", payload)
    damage = {
        str(row.get("name")): float(row.get("damage", 0))
        for row in result.get("attacks", [])
    }
    representatives = {}
    for family in ("normal", "skill", "burst"):
        eligible = [
            attack for attack in candidates
            if _attack_family(attack) == family
            and math.isfinite(damage.get(str(attack["name"]), 0))
            and damage.get(str(attack["name"]), 0) > 0
        ]
        if eligible:
            representatives[family] = max(
                eligible,
                key=lambda attack: damage.get(str(attack["name"]), 0),
            )
    return representatives


def calibrate_character(
    node: str,
    character: dict,
    *,
    seed: int = 0,
) -> list[dict]:
    primary = calibrate_one(node, character, seed=seed, is_default=True)
    if primary is None:
        return []
    go_key = _go_key(character["name_en"])
    profile = PROFILES.get(go_key, {})
    catalog = _call(node, "formulas", {"character": go_key})
    conditions = dict(profile.get("conditions") or {})
    if not conditions:
        conditions = _auto_conditions(catalog.get("conditions") or {})
    representatives = _representative_attacks(
        node,
        go_key,
        catalog.get("attacks", []),
        conditions,
        profile.get("weapon"),
    )
    primary_family = _attack_family({
        "name": primary["attack"],
        "category": primary["category"],
    })
    models = [primary]
    for family in ("normal", "skill", "burst"):
        attack = representatives.get(family)
        if (
            attack is None
            or attack["name"] == primary["attack"]
            or family == primary_family
        ):
            continue
        try:
            spec = calibrate_one(
                node,
                character,
                seed=seed,
                selected_attack=str(attack["name"]),
                is_default=False,
            )
        except Exception as exc:  # noqa: BLE001 - other calibrated nodes remain useful
            print(
                f"[skip] {character['name_en']} {family} {attack['name']}: {exc}",
                file=sys.stderr,
            )
            continue
        if spec is None:
            continue
        if spec["accuracy"] not in ("high", "medium"):
            print(
                f"[skip] {character['name_en']} {family} {attack['name']}: "
                f"校准误差不可用于综合评分 ({spec['accuracy']})",
                file=sys.stderr,
            )
            continue
        models.append(spec)
    return models


def main() -> None:
    parser = argparse.ArgumentParser(description="生成角色伤害模型目录")
    parser.add_argument("--node", default="node", help="Node.js 可执行文件")
    parser.add_argument("--only", action="append", default=[], help="只生成这些角色（中文或英文名）")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--seed", type=int, default=20261126)
    args = parser.parse_args()

    zh = _load_gzip("characters.zh.json.gz")
    en = _load_gzip("characters.en.json.gz")
    talents_en = _load_gzip("talents.en.json.gz")
    targets = []
    only = {name.casefold() for name in args.only}
    only_tokens = {_go_key(name).casefold() for name in args.only}
    for key, row in zh.items():
        if key in ("aether", "lumine", "manekin", "manekina"):
            continue
        name_en = en.get(key, {}).get("name", row.get("name", key))
        if only and not (
            key.casefold() in only
            or str(row.get("name", "")).casefold() in only
            or name_en.casefold() in only
            or _go_key(key).casefold() in only_tokens
        ):
            continue
        targets.append({
            "key": key,
            "name": row.get("name", key),
            "name_en": name_en,
            "element": str(row.get("elementType", "ELEMENT_NONE")).removeprefix("ELEMENT_"),
            "weapon_type": str(row.get("weaponType", "")).removeprefix("WEAPON_"),
            "region": row.get("region"),
        })
    for form in traveler_forms(language="zh"):
        key = form["key"]
        row = zh["aether"]
        name = form["name_zh"]
        name_en = talents_en[key]["name"]
        if only and "traveler" not in only and not any(
            candidate.casefold() in only or _go_key(candidate).casefold() in only_tokens
            for candidate in (key, name, name_en, form["name_en"], form["element"])
        ):
            continue
        targets.append({
            "key": key,
            "name": name,
            "name_en": name_en,
            "element": form["elementType"],
            "weapon_type": str(row.get("weaponType", "")).removeprefix("WEAPON_"),
            "region": row.get("region"),
        })

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(calibrate_character, args.node, character, seed=args.seed): character
            for character in targets
        }
        for future, character in futures.items():
            try:
                specs = future.result()
            except Exception as exc:  # noqa: BLE001
                print(f"[skip] {character['name_en']}: {exc}", file=sys.stderr)
                continue
            for spec in specs or []:
                results.append(spec)
                error = (spec.get("calibration") or {}).get("max_relative_error")
                print(
                    f"[ok] {spec['character']} {spec['attack']} "
                    f"set={spec['target_four_piece_set']} err={error if error is None else f'{error:.2%}'}"
                    f"{' [default]' if spec.get('is_default') else ''}",
                )

    results.sort(key=lambda item: (item["character"].casefold(), item["key"]))
    coverage = []
    for character in targets:
        rows = [spec for spec in results if spec["character"] == character["name_en"]]
        available_categories = sorted({
            _attack_family({"name": spec["attack"], "category": spec["category"]})
            for spec in rows
        })
        missing_categories = [
            category for category in ("normal", "skill", "burst")
            if category not in available_categories
        ]
        coverage.append({
            "character": character["name_en"],
            "character_zh": character["name"],
            "available_categories": available_categories,
            "missing_categories": missing_categories,
            "status": (
                "complete" if not missing_categories
                else "partial" if rows
                else "insufficient"
            ),
            "note": (
                "缺少的攻击类别没有通过校准可靠性门槛的上游节点。"
                if missing_categories else None
            ),
        })
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "upstream": "Genshin Optimizer 10.40.0 bridge (916e46d1)",
        "scenario": {
            "level": BASE_LEVEL,
            "ascension": BASE_ASCENSION,
            "constellation": 0,
            "talents": TALENTS,
            "enemy": {"level": 90, "resistance": RESISTANCE},
            "weapon": None,
        },
        "coverage": coverage,
        "models": results,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(results)} models to {args.output}")


if __name__ == "__main__":
    main()
