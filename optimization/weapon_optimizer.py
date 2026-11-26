"""Weapon-aware joint optimization.

Weapons are treated as unique resources, just like artifacts.  For every
character/weapon pair the catalog model is specialized with:

* the weapon's base stats and substat (through ``character_weapon_panel``);
* modeled passive panel stats (ATK%, HP%, ...);
* modeled passive DMG/CRIT bonuses for the character's damage category.

The module first evaluates every character/weapon pair with an independent
artifact search, chooses a unique weapon assignment, then runs the existing
joint artifact optimizer and a local weapon-swap pass.

Weapon passives are approximations at a fixed refinement; the exact values
used by the experiment are carried in :class:`WeaponOption` and reported.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from itertools import product
from math import isfinite
from typing import Mapping, Sequence

from core.models import Artifact
from damage.build_objective import character_weapon_panel
from damage.character_models import catalog_spec, model_from_spec
from damage.damage_models import DamageModel
from data.game_data import SETS, lookup
from .loadout_optimizer import Loadout, LoadoutPiece, evaluate_loadout, optimize_loadout
from .party_optimizer import PartyMember, PartyOptimizeResult, optimize_party


_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")
_ALL_CATEGORIES = frozenset({"normal", "charged", "plunge", "skill", "burst"})


@dataclass(frozen=True)
class WeaponOption:
    name: str
    weapon_type: str
    level: int = 90
    ascension: int = 6
    refinement: int = 1
    panel_bonus: Mapping[str, float] = field(default_factory=dict)
    dmg_bonus: float = 0.0
    crit_rate: float = 0.0
    crit_dmg: float = 0.0
    applies_to: frozenset[str] = _ALL_CATEGORIES
    notes: str = ""

    def as_panel(self) -> dict:
        return {"id": self.name, "level": self.level, "ascension": self.ascension}

    def describe(self) -> dict:
        return {
            "name": self.name,
            "weapon_type": self.weapon_type,
            "level": self.level,
            "refinement": self.refinement,
            "panel_bonus": dict(self.panel_bonus),
            "dmg_bonus": self.dmg_bonus,
            "crit_rate": self.crit_rate,
            "crit_dmg": self.crit_dmg,
            "applies_to": sorted(self.applies_to),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class WeaponMember:
    character: Mapping[str, object]
    base_model: DamageModel
    candidate_weapons: Sequence[WeaponOption]
    target_four_piece_set: int | None
    weight: float = 1.0
    name: str | None = None
    current_weapon: WeaponOption | None = None
    current_pieces: Sequence[LoadoutPiece] | None = None

    @property
    def label(self) -> str:
        return self.name or str(self.character.get("id") or self.base_model.character)

    def resolved_set(self) -> int | None:
        value = self.target_four_piece_set if self.target_four_piece_set is not None else self.base_model.target_four_piece_set
        if value is None:
            return None
        if self.base_model.target_four_piece_set is not None and value != self.base_model.target_four_piece_set:
            raise ValueError(
                f"模型 {self.base_model.key} 只覆盖套装 {self.base_model.target_four_piece_set}",
            )
        target = lookup(SETS, value)
        if 5 not in target["rarities"] or "4" not in target["effects"]:
            raise ValueError("目标四件套必须是五星四件套")
        return int(target["id"])


@dataclass(frozen=True)
class WeaponChoice:
    member: WeaponMember
    weapon: WeaponOption
    model: DamageModel
    loadout: Loadout
    damage: float
    independent_damage: float
    current_damage: float | None
    current_weapon: WeaponOption | None
    weapon_scores: tuple[dict, ...]

    def describe(self) -> dict:
        gain = None
        if self.current_damage and self.current_damage > 0:
            gain = (self.damage / self.current_damage - 1.0) * 100.0
        return {
            "name": self.member.label,
            "character": self.member.character.get("id"),
            "weapon": self.weapon.describe(),
            "current_weapon": self.current_weapon.name if self.current_weapon else None,
            "current_damage": self.current_damage,
            "damage": self.damage,
            "gain_pct": gain,
            "independent_damage": self.independent_damage,
            "target_four_piece_set": (
                {"id": self.member.resolved_set(), "name": SETS[self.member.resolved_set()]["name"]}
                if self.member.resolved_set() is not None else None
            ),
            "set_names": [SETS[artifact.set_id]["name"] for artifact in self.loadout.artifacts()],
            "loadout": self.loadout.describe(),
            "weapon_scores": list(self.weapon_scores),
        }


@dataclass(frozen=True)
class WeaponOptimizeResult:
    members: tuple[WeaponChoice, ...]
    unused_weapons: tuple[WeaponOption, ...]
    joint: PartyOptimizeResult
    public_weapons: tuple[WeaponOption, ...]
    weapon_swaps: int
    warnings: tuple[str, ...]

    def describe(self) -> dict:
        return {
            "members": [choice.describe() for choice in self.members],
            "assignment": {choice.member.label: choice.weapon.name for choice in self.members},
            "unused_weapons": [weapon.describe() for weapon in self.unused_weapons],
            "public_weapons": [weapon.describe() for weapon in self.public_weapons],
            "joint_search": self.joint.describe()["search"],
            "weapon_swaps": self.weapon_swaps,
            "warnings": list(self.warnings),
        }


def weapon_variant(base_model: DamageModel, option: WeaponOption) -> DamageModel:
    """Specialize a catalog model with one weapon's base stats and passive."""
    try:
        spec = deepcopy(catalog_spec(base_model.key))
    except ValueError as exc:
        raise ValueError(f"武器优化只支持目录模型，{base_model.key} 不可用") from exc
    category = str(spec.get("category") or "normal")
    spec["panel_bonus_stats"] = {
        **dict(spec.get("panel_bonus_stats") or {}),
        **{key: float(value) for key, value in option.panel_bonus.items()},
    }
    if category in option.applies_to:
        spec["dmg_bonus"] = float(spec.get("dmg_bonus", 0.0)) + option.dmg_bonus
        spec["crit_rate_bonus"] = float(spec.get("crit_rate_bonus", 0.0)) + option.crit_rate
        spec["crit_dmg_bonus"] = float(spec.get("crit_dmg_bonus", 0.0)) + option.crit_dmg
    spec["assumptions"] = dict(spec.get("assumptions") or {})
    spec["assumptions"]["weapon"] = option.name
    spec["assumptions"]["weapon_refinement"] = option.refinement
    spec["assumptions"]["weapon_passive"] = option.notes or "近似被动"
    spec["weapon"] = option.name
    spec["key"] = f"{base_model.key}__{option.name.casefold().replace(' ', '_')}"
    spec["label"] = f"{base_model.label} · {option.name}"
    return model_from_spec(spec, str(base_model.assumptions.get("crit_mode") or "average"))


def member_weapon_panel(member: WeaponMember, model: DamageModel, option: WeaponOption) -> dict:
    bonus = dict(model.panel_bonus_stats)
    stats, _ = character_weapon_panel(member.character, option.as_panel(), bonus)
    return stats


def evaluate_member_weapon(
    member: WeaponMember,
    option: WeaponOption,
    pieces: Sequence[LoadoutPiece],
) -> tuple[Loadout, DamageModel]:
    model = weapon_variant(member.base_model, option)
    panel = member_weapon_panel(member, model, option)
    loadout = evaluate_loadout(list(pieces), model, panel)
    return loadout, model


def _normal_pieces(pieces: Sequence[LoadoutPiece | object]) -> list[LoadoutPiece]:
    result = []
    for item in pieces:
        if isinstance(item, LoadoutPiece):
            result.append(item)
        elif isinstance(item, Artifact):
            result.append(LoadoutPiece(item))
        else:
            raise TypeError("库存只能包含 Artifact 或 LoadoutPiece")
    return result


def _choose_assignment(
    members: Sequence[WeaponMember],
    evaluations: Mapping[tuple[int, int], dict],
    best_damage: Sequence[float],
    normalize: bool,
) -> tuple[int, ...]:
    best = None
    lengths = [len(member.candidate_weapons) for member in members]
    for combo in product(*[range(length) for length in lengths]):
        names = [members[index].candidate_weapons[choice].name for index, choice in enumerate(combo)]
        if len(set(names)) != len(names):
            continue
        normalized = 0.0
        absolute = 0.0
        for index, choice in enumerate(combo):
            damage = evaluations[(index, choice)]["damage"]
            normalized += members[index].weight * (
                damage / best_damage[index] if best_damage[index] > 0 else damage
            )
            absolute += members[index].weight * damage
        key = (normalized, absolute) if normalize else (absolute, normalized)
        if best is None or key > best[0]:
            best = (key, combo)
    if best is None:
        raise ValueError("没有可行的唯一武器分配")
    return best[1]


def optimize_weapons_and_artifacts(
    pieces: Sequence[LoadoutPiece | Artifact],
    members: Sequence[WeaponMember],
    *,
    max_nodes: int = 100_000,
    restarts: int = 1,
    max_iterations: int = 1,
    seed: int = 0,
    normalize: bool = False,
) -> WeaponOptimizeResult:
    """Choose one unique weapon and five artifacts for every member."""
    if not members:
        raise ValueError("至少需要一个角色")
    normalized = _normal_pieces(pieces)
    for member in members:
        if not member.candidate_weapons:
            raise ValueError(f"{member.label} 没有候选武器")
        if type(member.weight) not in (int, float) or not isfinite(member.weight) or member.weight <= 0:
            raise ValueError("角色权重必须是有限正数")
    targets = [member.resolved_set() for member in members]

    evaluations: dict[tuple[int, int], dict] = {}
    for index, member in enumerate(members):
        for choice, option in enumerate(member.candidate_weapons):
            model = weapon_variant(member.base_model, option)
            panel = member_weapon_panel(member, model, option)
            result = optimize_loadout(
                normalized, model, panel,
                required_four_piece_set=targets[index], max_nodes=max_nodes,
            )
            evaluations[(index, choice)] = {
                "model": model,
                "panel": panel,
                "loadout": result.loadout,
                "damage": result.loadout.damage,
                "exact": result.exact,
                "nodes": result.nodes,
            }
    best_damage = [
        max(evaluations[(index, choice)]["damage"] for choice in range(len(member.candidate_weapons)))
        for index, member in enumerate(members)
    ]
    assignment = list(_choose_assignment(members, evaluations, best_damage, normalize))

    party_members = [
        PartyMember(
            model=evaluations[(index, assignment[index])]["model"],
            character=member.character,
            weapon=member.candidate_weapons[assignment[index]].as_panel(),
            weight=member.weight,
            target_four_piece_set=targets[index],
            name=member.label,
        )
        for index, member in enumerate(members)
    ]
    joint = optimize_party(
        normalized, party_members,
        max_nodes=max_nodes, restarts=restarts, max_iterations=max_iterations,
        normalize=normalize, seed=seed,
    )

    # Local weapon swaps against the joint artifact loadouts.  This catches
    # cases where the independent weapon ranking differs after contention.
    weapon_swaps = 0
    used_names = {members[index].candidate_weapons[assignment[index]].name for index in range(len(members))}
    for index, member in enumerate(members):
        current_choice = assignment[index]
        current_loadout = joint.members[index].loadout
        current_weapon = member.candidate_weapons[current_choice]
        current_score = member.weight * (
            current_loadout.damage / best_damage[index]
            if normalize and best_damage[index] > 0 else current_loadout.damage
        )
        best_alt = None
        for choice, option in enumerate(member.candidate_weapons):
            if choice == current_choice or option.name in used_names:
                continue
            evaluation = evaluations[(index, choice)]
            candidate = evaluate_loadout(
                [piece for piece in current_loadout.pieces if piece is not None],
                evaluation["model"], evaluation["panel"],
            )
            score = member.weight * (
                candidate.damage / best_damage[index]
                if normalize and best_damage[index] > 0 else candidate.damage
            )
            if score > current_score + 1e-12 and (best_alt is None or score > best_alt[0]):
                best_alt = (score, choice, candidate)
        if best_alt is not None:
            _, choice, candidate = best_alt
            option = member.candidate_weapons[choice]
            used_names.remove(current_weapon.name)
            used_names.add(option.name)
            assignment[index] = choice
            party_members[index] = PartyMember(
                model=evaluations[(index, choice)]["model"],
                character=member.character,
                weapon=option.as_panel(),
                weight=member.weight,
                target_four_piece_set=targets[index],
                name=member.label,
            )
            joint = optimize_party(
                normalized, party_members,
                max_nodes=max_nodes, restarts=restarts, max_iterations=max_iterations,
                normalize=normalize, seed=seed + 1,
            )
            weapon_swaps += 1

    warnings = ["武器被动按固定精炼与近似覆盖值计入；实际毕业伤害可能不同。"]
    if joint.conflicts:
        warnings.append("武器与圣遗物联合分配存在争用，使用启发式搜索。")
    if any(not evaluations[(index, assignment[index])]["exact"] for index in range(len(members))):
        warnings.append("部分独立武器评估达到节点上限。")

    choices = []
    all_weapons: dict[str, WeaponOption] = {}
    for index, member in enumerate(members):
        for option in member.candidate_weapons:
            all_weapons[option.name] = option
        option = member.candidate_weapons[assignment[index]]
        evaluation = evaluations[(index, assignment[index])]
        scores = tuple(
            {
                "weapon": candidate.name,
                "independent_damage": evaluations[(index, choice)]["damage"],
                "passive": candidate.notes,
            }
            for choice, candidate in sorted(
                enumerate(member.candidate_weapons),
                key=lambda item: evaluations[(index, item[0])]["damage"],
                reverse=True,
            )
        )
        choices.append(WeaponChoice(
            member=member,
            weapon=option,
            model=evaluation["model"],
            loadout=joint.members[index].loadout,
            damage=joint.members[index].loadout.damage,
            independent_damage=evaluation["damage"],
            current_damage=None,
            current_weapon=member.current_weapon,
            weapon_scores=scores,
        ))
    used = {choice.weapon.name for choice in choices}
    unused = tuple(option for name, option in all_weapons.items() if name not in used)
    return WeaponOptimizeResult(
        members=tuple(choices),
        unused_weapons=unused,
        joint=joint,
        public_weapons=tuple(all_weapons.values()),
        weapon_swaps=weapon_swaps,
        warnings=tuple(warnings),
    )
