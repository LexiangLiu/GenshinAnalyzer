#!/usr/bin/env python3
"""Build the version-pinned Manekin combat parameter supplement."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
from pathlib import Path


SOURCE_COMMIT = "f2800e32599f32cde76ae0b4b93ea30b75651e59"
CROSSCHECK_COMMIT = "4c8484ff9c6270db90ff581d7a584b06d035829d"
ELEMENT_MAPPING_COMMIT = "4a70bdfdd60eecc69a9e3e23ae540c77fd17dfbd"
ELEMENT_MAPPING_REVISION = "b061b403c8afc7bca633cf4f201edc4a3baa75fe"
ELEMENT_MAPPING_RAW_COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
GO_COMMIT = "916e46d14d1031c9188a6122f045f3308b0a1f37"
GO_VERSION = "10.40.0"

SOURCE_SHA256 = {
    "AvatarSkillDepotExcelConfigData.json": "7e91c34c75387a6926c13710caa1d6829271453224743b2f28cbbc0ebb35acdd",
    "AvatarSkillExcelConfigData.json": "5258ed90da6372ea9da572748252775deb4089e908bb0a96c9067eced28eb6ec",
    "ProudSkillExcelConfigData.json": "c0083146091ee6968bcf06c5143196eaec4130b0cf105ec0c49bcdc56ae766bc",
}
CROSSCHECK_SHA256 = {
    "manekin.json": "e5c8810b3f07e3ca3ead45b94b7394d2ca367a0f6b50b0387d57932d4283b9ae",
    "manekina.json": "89d2cd203d1e6ef160a09bb1a3d75859b3c5fbf90f8416af30b28811b3ee7d61",
}
ELEMENT_MAPPING_SHA256 = "a9c18750f8eaaf25ed9131bdba98083a6b073190222347670bb0fd01dbf63854"

ELEMENTS = (
    ("anemo", 11706, 11806, 111755),
    ("geo", 11707, 11807, 111756),
    ("electro", 11704, 11804, 111753),
    ("dendro", 11708, 11808, 111757),
    ("hydro", 11703, 11803, 111752),
    ("pyro", 11702, 11802, 111751),
    ("cryo", 11705, 11805, 111754),
)

SKILLS = {
    "normal_attack": {
        "id": 11171,
        "group": 11731,
        "parameters": (
            ("normal_hit_1", 0, "damage_multiplier"),
            ("normal_hit_2", 1, "damage_multiplier"),
            ("normal_hit_3", 2, "damage_multiplier"),
            ("normal_hit_4", 3, "damage_multiplier"),
            ("charged_hit_1", 4, "damage_multiplier"),
            ("charged_hit_2", 5, "damage_multiplier"),
            ("charged_stamina_cost", 6, "stamina"),
            ("plunge_damage", 7, "damage_multiplier"),
            ("low_plunge_damage", 8, "damage_multiplier"),
            ("high_plunge_damage", 9, "damage_multiplier"),
        ),
    },
    "elemental_skill": {
        "id": 11172,
        "group": 11732,
        "parameters": (
            ("skill_damage", 0, "damage_multiplier"),
            ("infusion_duration_seconds", 1, "seconds"),
            ("cooldown_seconds", 2, "seconds"),
        ),
    },
    "elemental_burst": {
        "group": 11739,
        "parameters": (
            ("restricted_area_summon_damage", 0, "damage_multiplier"),
            ("restricted_area_trespass_damage", 1, "damage_multiplier"),
            ("restricted_area_duration_seconds", 2, "seconds"),
            ("cooldown_seconds", 3, "seconds"),
            ("energy_cost", 4, "energy"),
        ),
    },
}

PASSIVES = (
    {
        "key": "battlelines_redrawn",
        "group": 11721,
        "unlock_level": 1,
        "trigger": {
            "event": "character_leaves_field",
            "condition": "restricted_area_active",
            "damage_element": "current_adaptive_element",
        },
        "effect": "deal_aoe_damage_using_attack_multiplier",
        "parameters": {"off_field_explosion_attack_multiplier": 0},
    },
    {
        "key": "combat_readiness",
        "group": 11722,
        "unlock_level": 4,
        "trigger": {
            "event": "off_field",
            "condition": "energy_below_threshold",
        },
        "effect": "restore_energy_per_second",
        "parameters": {
            "off_field_energy_threshold": 0,
            "energy_regeneration_per_second": 1,
        },
    },
    {
        "key": "agile_stance",
        "group": 11725,
        "unlock_level": None,
        "trigger": {"event": "elemental_skill_or_burst_cast"},
        "effect": "randomly_switch_to_owned_cosmetic_plan",
        "parameters": {},
    },
)

TEXT_CROSSCHECK_LAYOUT = {
    11171: {
        "1-Hit DMG": (0,),
        "2-Hit DMG": (1,),
        "3-Hit DMG": (2,),
        "4-Hit DMG": (3,),
        "Charged Attack DMG": (4, 5),
        "Charged Attack Stamina Cost": (6,),
        "Plunge DMG": (7,),
        "Low/High Plunge DMG": (8, 9),
    },
    11172: {
        "Skill DMG": (0,),
        "Elemental Type Conversion Duration": (1,),
        "CD": (2,),
    },
    111757: {
        "Restricted Area Summon DMG": (0,),
        "Restricted Area Trespass DMG": (1,),
        "Restricted Area Duration": (2,),
        "CD": (3,),
        "Energy Cost": (4,),
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_pinned_json(folder: Path, filename: str, expected_hash: str) -> object:
    path = folder / filename
    actual_hash = _sha256(path)
    if actual_hash != expected_hash:
        raise ValueError(f"{path} SHA-256 mismatch: expected {expected_hash}, got {actual_hash}")
    return json.loads(path.read_text(encoding="utf-8"))


def _index(rows: list[dict], field: str, label: str) -> dict[int, dict]:
    result = {}
    for row in rows:
        key = row.get(field)
        if type(key) is not int:
            continue
        if key in result:
            raise ValueError(f"duplicate {label} {key}")
        result[key] = row
    return result


def _finite_params(row: dict, *, expected_count: int = 25) -> list[float]:
    params = row.get("paramList")
    if not isinstance(params, list) or len(params) != expected_count:
        raise ValueError(f"invalid parameter list at row {row.get('proudSkillId')}")
    if not all(type(value) in (int, float) and math.isfinite(value) for value in params):
        raise ValueError(f"non-finite parameter at row {row.get('proudSkillId')}")
    return [float(value) for value in params]


def _skill_record(
    skill_row: dict,
    proud_rows: dict[int, dict],
    skill_key: str,
    group_id: int,
) -> dict:
    levels = sorted(
        (row for row in proud_rows.values() if row.get("proudSkillGroupId") == group_id),
        key=lambda row: row.get("level", 0),
    )
    if [row.get("level") for row in levels] != list(range(1, 16)):
        raise ValueError(f"skill group {group_id} must have levels 1 through 15")
    parameters = SKILLS[skill_key]["parameters"]
    rank_data = []
    for row in levels:
        raw = _finite_params(row)
        rank_data.append({
            "level": row["level"],
            "parameters": {key: raw[index] for key, index, _unit in parameters},
            "raw_param_list": raw,
        })
    return {
        "skill_id": skill_row["id"],
        "proud_skill_group_id": group_id,
        "name_text_map_hash": skill_row["nameTextMapHash"],
        "description_text_map_hash": skill_row["descTextMapHash"],
        "ability_name": skill_row.get("abilityName"),
        "cooldown_seconds": skill_row.get("cdTime"),
        "energy_cost": skill_row.get("costElemVal"),
        "max_charges": skill_row.get("maxChargeNum"),
        "parameter_layout": [
            {"key": key, "index": index, "unit": unit}
            for key, index, unit in parameters
        ],
        "ranks": rank_data,
    }


def _passive_records(proud_rows: dict[int, dict], depots: dict[int, dict]) -> list[dict]:
    expected_unlocks = {(11721, 1), (11722, 4), (11725, None)}
    relevant_depots = set(range(11702, 11709)) | set(range(11802, 11809))
    validated_depots = set()
    for depot_id, depot in depots.items():
        if depot_id not in relevant_depots:
            continue
        unlocked = {
            (entry.get("proudSkillGroupId"), entry.get("needAvatarPromoteLevel"))
            for entry in depot.get("inherentProudSkillOpens", [])
            if entry.get("proudSkillGroupId") in {row["group"] for row in PASSIVES}
        }
        if unlocked != expected_unlocks:
            raise ValueError(f"unexpected Manekin passive unlocks in depot {depot_id}")
        validated_depots.add(depot_id)
    if validated_depots != relevant_depots:
        raise ValueError("pinned game tables are missing Manekin passive depot records")

    result = []
    for definition in PASSIVES:
        group_id = definition["group"]
        rows = [
            row for row in proud_rows.values()
            if row.get("proudSkillGroupId") == group_id
        ]
        if len(rows) != 1 or rows[0].get("level") != 1:
            raise ValueError(f"passive group {group_id} must have one rank")
        raw = _finite_params(rows[0])
        result.append({
            "key": definition["key"],
            "proud_skill_group_id": group_id,
            "proud_skill_id": rows[0]["proudSkillId"],
            "unlock_at_ascension": definition["unlock_level"],
            "trigger": definition["trigger"],
            "name_text_map_hash": rows[0]["nameTextMapHash"],
            "description_text_map_hash": rows[0]["descTextMapHash"],
            "parameters": {
                key: raw[index] for key, index in definition["parameters"].items()
            },
            "effect": definition["effect"],
            "raw_param_list": raw,
        })
    return result


def _numeric_values(value: str) -> list[float]:
    return [float(match) for match in re.findall(r"[-+]?(?:\d+\.\d+|\d+|\.\d+)", value)]


def _crosscheck_attributes(
    char_path: Path,
    expected_hash: str,
    skill_groups: dict[int, dict[int, dict]],
) -> None:
    if _sha256(char_path) != expected_hash:
        raise ValueError(f"{char_path} SHA-256 mismatch")
    character = json.loads(char_path.read_text(encoding="utf-8"))
    skills = {row.get("_id"): row for row in character.get("skills", [])}
    for skill_id, labels in TEXT_CROSSCHECK_LAYOUT.items():
        if skill_id not in skills:
            raise ValueError(f"{char_path.name} is missing skill {skill_id}")
        skill = skills[skill_id]
        attributes = {row.get("label"): row for row in skill.get("attributes", [])}
        group_id = next(
            group for group, skill_info in (
                (11731, 11171), (11732, 11172), (11739, 111757),
            ) if skill_info == skill_id
        )
        ranks = skill_groups[group_id]
        if set(labels) - set(attributes):
            raise ValueError(f"{char_path.name} is missing expected attributes for {skill_id}")
        for label, param_indices in labels.items():
            values = attributes[label].get("values", [])
            if len(values) != 15:
                raise ValueError(f"{char_path.name} {label} must have 15 ranks")
            for rank, text_value in enumerate(values, start=1):
                actual = _numeric_values(text_value)
                expected = [ranks[rank]["paramList"][index] for index in param_indices]
                if label.endswith("DMG"):
                    expected = [value * 100 for value in expected]
                if len(actual) < len(expected) or any(
                    round(actual[index], 1) != round(expected_value, 1)
                    for index, expected_value in enumerate(expected)
                ):
                    raise ValueError(
                        f"{char_path.name} {skill_id}/{label}/rank {rank} does not match game parameters"
                    )


def _crosscheck_mechanics(char_path: Path, expected_hash: str) -> None:
    if _sha256(char_path) != expected_hash:
        raise ValueError(f"{char_path} SHA-256 mismatch")
    character = json.loads(char_path.read_text(encoding="utf-8"))
    skills = {row.get("_id"): row for row in character.get("skills", [])}
    burst_description = str(skills.get(111757, {}).get("description", "")).casefold()
    if "once every 0.5s" not in burst_description:
        raise ValueError(f"{char_path.name} does not verify the burst entry-damage interval")
    passive_descriptions = {
        row.get("id"): str(row.get("description", "")).casefold()
        for row in character.get("passives", [])
    }
    expected_phrases = {
        "battlelines_redrawn": ("leaves the field", "200%"),
        "combat_readiness": ("below 30%", "2 elemental energy per second"),
        "agile_stance": ("elemental skill or burst", "at random"),
    }
    for passive, phrases in expected_phrases.items():
        description = passive_descriptions.get(passive, "")
        if any(phrase not in description for phrase in phrases):
            raise ValueError(f"{char_path.name} does not verify passive {passive}")


def build_payload(
    source_dir: Path,
    crosscheck_dir: Path,
    element_mapping_file: Path,
) -> tuple[dict, dict]:
    source_files = {
        filename: _read_pinned_json(source_dir, filename, expected)
        for filename, expected in SOURCE_SHA256.items()
    }
    depot_rows = _index(
        source_files["AvatarSkillDepotExcelConfigData.json"], "id", "skill depot",
    )
    skill_rows = _index(
        source_files["AvatarSkillExcelConfigData.json"], "id", "skill",
    )
    proud_rows = _index(
        source_files["ProudSkillExcelConfigData.json"], "proudSkillId", "proud skill",
    )
    selected_depot_ids = {
        depot_id for _element, male_id, female_id, _burst_id in ELEMENTS
        for depot_id in (male_id, female_id)
    }
    if not selected_depot_ids.issubset(depot_rows):
        raise ValueError("pinned game tables are missing required Manekin skill depots")
    required_groups = {11721, 11722, 11725, 11731, 11732, 11739}
    selected_proud_rows = {
        row["proudSkillId"]: row for row in proud_rows.values()
        if row.get("proudSkillGroupId") in required_groups
    }
    selected_by_level = {
        group: {
            row["level"]: row
            for row in selected_proud_rows.values()
            if row.get("proudSkillGroupId") == group
        }
        for group in required_groups
    }
    for group in (11731, 11732, 11739):
        if set(selected_by_level[group]) != set(range(1, 16)):
            raise ValueError(f"pinned game tables do not contain all 15 ranks for group {group}")

    if _sha256(element_mapping_file) != ELEMENT_MAPPING_SHA256:
        raise ValueError(f"{element_mapping_file} SHA-256 mismatch")
    element_mapping = json.loads(element_mapping_file.read_text(encoding="utf-8"))
    if (
        element_mapping.get("revision") != ELEMENT_MAPPING_REVISION
        or element_mapping.get("rawReleaseCommit") != ELEMENT_MAPPING_RAW_COMMIT
        or element_mapping.get("evidenceStatus") != "source-provisional"
    ):
        raise ValueError("the element mapping must use the pinned provisional raw-data revision")
    expected_depots = {
        element: {"manekin": male, "manekina": female}
        for element, male, female, _burst in ELEMENTS
    }
    mapped_depots = {"manekin": {}, "manekina": {}}
    for template in element_mapping.get("templates", []):
        appearance = template.get("characterSlug")
        variant_id = template.get("variantId", "")
        if appearance not in mapped_depots or template.get("abilityKey") != "combat2":
            continue
        prefix = f"{appearance}-"
        if not variant_id.startswith(prefix):
            continue
        mapped_depots[appearance][variant_id.removeprefix(prefix)] = template.get("skillDepotId")
    for appearance, expected in (
        ("manekin", {key: value["manekin"] for key, value in expected_depots.items()}),
        ("manekina", {key: value["manekina"] for key, value in expected_depots.items()}),
    ):
        if mapped_depots[appearance] != expected:
            raise ValueError(f"provisional element mapping disagrees for {appearance}")

    _crosscheck_attributes(
        crosscheck_dir / "manekin.json",
        CROSSCHECK_SHA256["manekin.json"],
        selected_by_level,
    )
    _crosscheck_attributes(
        crosscheck_dir / "manekina.json",
        CROSSCHECK_SHA256["manekina.json"],
        selected_by_level,
    )
    _crosscheck_mechanics(
        crosscheck_dir / "manekin.json",
        CROSSCHECK_SHA256["manekin.json"],
    )
    _crosscheck_mechanics(
        crosscheck_dir / "manekina.json",
        CROSSCHECK_SHA256["manekina.json"],
    )

    normal = _skill_record(skill_rows[11171], selected_proud_rows, "normal_attack", 11731)
    skill = _skill_record(skill_rows[11172], selected_proud_rows, "elemental_skill", 11732)
    element_rows = {}
    for element, male_depot_id, female_depot_id, burst_skill_id in ELEMENTS:
        male_depot = depot_rows[male_depot_id]
        female_depot = depot_rows[female_depot_id]
        for depot_id, depot in ((male_depot_id, male_depot), (female_depot_id, female_depot)):
            if depot.get("skills", [])[:2] != [11171, 11172] or depot.get("energySkill") != burst_skill_id:
                raise ValueError(f"unexpected skill mapping for depot {depot_id}")
        burst_row = skill_rows[burst_skill_id]
        if burst_row.get("proudSkillGroupId") != 11739:
            raise ValueError(f"burst skill {burst_skill_id} is not linked to group 11739")
        burst = _skill_record(burst_row, selected_proud_rows, "elemental_burst", 11739)
        burst["triggers"] = [
            {
                "event": "restricted_area_summoned",
                "damage_parameter": "restricted_area_summon_damage",
                "damage_element": "current_adaptive_element",
            },
            {
                "event": "opponent_enters_restricted_area",
                "interval_seconds": 0.5,
                "damage_parameter": "restricted_area_trespass_damage",
                "damage_element": "current_adaptive_element",
            },
        ]
        element_rows[element] = {
            "element_type": f"ELEMENT_{element.upper()}",
            "skill_depot_ids": {
                "manekin": male_depot_id,
                "manekina": female_depot_id,
            },
            "skills": {
                "normal_attack": normal,
                "elemental_skill": skill,
                "elemental_burst": burst,
            },
            "damage_model": {
                "status": "uncalibrated",
                "scoring_eligible": False,
                "reason": (
                    "The pinned Genshin Optimizer bundle has no Manekin formula node; "
                    "this element is excluded from loadout scoring."
                ),
            },
        }

    payload = {
        "schema_version": 1,
        "game_version": "7.2",
        "identity": "manekin",
        "adaptive_element_source": "traveler_current_element",
        "appearances": {
            "manekin": {"character_id": 10000117},
            "manekina": {"character_id": 10000118},
        },
        "elements": element_rows,
        "passives": _passive_records(selected_proud_rows, depot_rows),
        "constellations": [],
    }
    provenance = {
        "source": {
            "repository": "MeChen618/AstaPS-Resource",
            "commit": SOURCE_COMMIT,
            "game_version": "7.2",
            "url": f"https://github.com/MeChen618/AstaPS-Resource/tree/{SOURCE_COMMIT}",
            "license": "MIT",
            "input_sha256": SOURCE_SHA256,
        },
        "crosscheck": {
            "repository": "dvaJi/genshin-data",
            "commit": CROSSCHECK_COMMIT,
            "url": f"https://github.com/dvaJi/genshin-data/tree/{CROSSCHECK_COMMIT}",
            "files_sha256": CROSSCHECK_SHA256,
            "result": "all 15 ranks for normal attack, skill, and burst match to displayed precision for both appearances",
            "mechanics_result": "No Entry's 0.5s entry interval and all three passive trigger descriptions match both appearance records",
        },
        "element_mapping": {
            "repository": "mixuanda/genshin-elemental-reaction-sim",
            "commit": ELEMENT_MAPPING_COMMIT,
            "revision": ELEMENT_MAPPING_REVISION,
            "raw_release_commit": ELEMENT_MAPPING_RAW_COMMIT,
            "file": "packages/game-data/sources/manekin-raw-casts/definitions.json",
            "file_sha256": ELEMENT_MAPPING_SHA256,
            "evidence_status": "source-provisional",
            "result": "all 14 named appearance-element templates map to the matching pinned raw skill depot IDs",
        },
        "calibration": {
            "upstream": "Genshin Optimizer",
            "version": GO_VERSION,
            "commit": GO_COMMIT,
            "status": "unavailable",
            "reason": "The pinned bundle has no Manekin or Manekina damage formula nodes.",
            "elements": {element: "uncalibrated" for element, *_rest in ELEMENTS},
        },
    }
    return payload, provenance


def build(
    source_dir: Path,
    crosscheck_dir: Path,
    element_mapping_file: Path,
    output_dir: Path,
) -> dict:
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("game_version") != "7.2":
        raise ValueError("the Manekin supplement is pinned to game version 7.2")
    payload, provenance = build_payload(source_dir, crosscheck_dir, element_mapping_file)
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    packed = gzip.compress(content, compresslevel=9, mtime=0)
    output_path = output_dir / "manekin.combat.json.gz"
    temporary_data = output_path.with_name(output_path.name + ".tmp")
    temporary_data.write_bytes(packed)
    temporary_data.replace(output_path)
    manifest.setdefault("supplements", {})["manekin_combat"] = {
        "file": output_path.name,
        "sha256": hashlib.sha256(packed).hexdigest(),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "schema_version": payload["schema_version"],
        "game_version": payload["game_version"],
        "source": provenance["source"],
        "crosscheck": provenance["crosscheck"],
        "element_mapping": provenance["element_mapping"],
        "calibration": provenance["calibration"],
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary_manifest = manifest_path.with_name(manifest_path.name + ".tmp")
    temporary_manifest.write_bytes(manifest_bytes)
    temporary_manifest.replace(manifest_path)
    return {
        "file": str(output_path),
        "elements": list(payload["elements"]),
        "rank_count": len(payload["elements"]["anemo"]["skills"]["normal_attack"]["ranks"]),
        "calibration": provenance["calibration"]["status"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="directory with the pinned raw ExcelBinOutput JSON files")
    parser.add_argument("--crosscheck-dir", type=Path, required=True, help="directory with pinned English manekin.json and manekina.json")
    parser.add_argument(
        "--element-mapping-file",
        type=Path,
        required=True,
        help="pinned raw-casts definitions.json used to map element names to skill depots",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "reference_data",
    )
    args = parser.parse_args()
    print(json.dumps(build(
        args.source_dir,
        args.crosscheck_dir,
        args.element_mapping_file,
        args.output_dir,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
