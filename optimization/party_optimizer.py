"""Joint artifact assignment for several characters sharing one inventory.

Each character needs exactly one artifact per slot and every artifact can be
equipped by only one character.  With independent best loadouts the only
coupling is artifact contention, so the optimizer:

1. computes every member's unconstrained best loadout;
2. returns immediately when no artifact is contested (this is globally optimal);
3. otherwise tries several greedy assignment orders and improves the result
   with coordinate ascent and same-slot swaps.

The last two steps are heuristic; ``exact`` is false whenever contention
exists.  The search is deliberately small and deterministic so it can run on
an ordinary inventory without external solvers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

from damage.build_objective import character_weapon_panel
from damage.damage_models import DamageModel
from data.game_data import SETS, lookup
from .loadout_optimizer import Loadout, LoadoutPiece, evaluate_loadout, optimize_loadout


_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")


@dataclass(frozen=True)
class PartyMember:
    model: DamageModel
    character: Mapping[str, object]
    weapon: Mapping[str, object] | None = None
    bonus_stats: Mapping[str, float] | None = None
    weight: float = 1.0
    target_four_piece_set: int | None = None
    fixed_four_piece_set: int | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if type(self.weight) not in (int, float) or not isfinite(self.weight) or self.weight <= 0:
            raise ValueError("角色权重必须是有限正数")
        if self.bonus_stats is not None and not isinstance(self.bonus_stats, dict):
            raise ValueError("bonus_stats 必须是属性名到数值的对象")
        if self.target_four_piece_set is not None and not isinstance(self.target_four_piece_set, int):
            raise ValueError("target_four_piece_set 必须是套装 ID")
        if self.fixed_four_piece_set is not None and type(self.fixed_four_piece_set) is not int:
            raise ValueError("fixed_four_piece_set 必须是套装 ID")
        selected_set = (
            self.fixed_four_piece_set
            if self.fixed_four_piece_set is not None
            else self.target_four_piece_set
        )
        if selected_set is not None and selected_set != self.model.target_four_piece_set:
            from damage.character_models import catalog_spec, get_catalog_model

            try:
                catalog_spec(self.model.key)
            except ValueError:
                return
            assumptions = self.model.assumptions
            object.__setattr__(
                self,
                "model",
                get_catalog_model(
                    self.model.key,
                    crit_mode=str(assumptions.get("crit_mode", "average")),
                    talents=assumptions.get("talents"),
                    target_four_piece_set=selected_set,
                ),
            )

    @property
    def label(self) -> str:
        return self.name or str(self.character.get("id") or self.model.character)

    def resolved_set(self) -> int | None:
        if self.fixed_four_piece_set is not None:
            value = self.fixed_four_piece_set
        else:
            value = self.target_four_piece_set if self.target_four_piece_set is not None else self.model.target_four_piece_set
            if self.model.target_four_piece_set is not None and value != self.model.target_four_piece_set:
                raise ValueError(
                    f"伤害模型 {self.model.key} 只覆盖套装 {self.model.target_four_piece_set}，"
                    f"不能改用 {value}",
                )
        if value is None:
            return None
        target = lookup(SETS, value)
        if 5 not in target["rarities"] or "4" not in target["effects"]:
            raise ValueError("目标四件套必须是有四件套效果的五星套装")
        return int(target["id"])

    def panel(self) -> dict:
        bonus = dict(self.model.panel_bonus_stats if self.bonus_stats is None else self.bonus_stats)
        fixed = self.model.assumptions.get("weapon")
        if fixed is not None:
            if self.weapon is None:
                raise ValueError(f"{self.label} 的模型 {self.model.key} 需要武器「{fixed}」")
            from damage.character_models import weapon_matches
            if not weapon_matches(self.weapon.get("id"), fixed):
                raise ValueError(f"{self.label} 的模型 {self.model.key} 只覆盖武器「{fixed}」")
        stats, _ = character_weapon_panel(self.character, self.weapon, bonus)
        return stats


@dataclass(frozen=True)
class PartyLoadout:
    member: PartyMember
    loadout: Loadout
    reference_damage: float
    target_four_piece_set: int | None

    def score(self, normalize: bool) -> float:
        if normalize and self.reference_damage > 0:
            return self.loadout.damage / self.reference_damage
        return self.loadout.damage

    def describe(self, normalize: bool) -> dict:
        target = self.target_four_piece_set
        result = {
            "name": self.member.label,
            "character": self.member.model.character,
            "model": self.member.model.key,
            "weight": float(self.member.weight),
            "target_four_piece_set": (
                {"id": target, "name": SETS[target]["name"]} if target is not None else None
            ),
            "damage": self.loadout.damage,
            "reference_damage": self.reference_damage,
            "normalized_damage": (
                self.loadout.damage / self.reference_damage if self.reference_damage > 0 else None
            ),
            "score": self.score(normalize),
            "loadout": self.loadout.describe(),
        }
        breakdown = getattr(self.member.model, "score_breakdown", None)
        if callable(breakdown):
            result["formula_objective"] = breakdown(
                self.loadout.stats,
                self.loadout.set_flags,
            )
        return result


@dataclass(frozen=True)
class PartyOptimizeResult:
    members: tuple[PartyLoadout, ...]
    total_objective: float
    exact: bool
    iterations: int
    conflicts: int
    unused: tuple[LoadoutPiece, ...]
    warnings: tuple[str, ...]
    normalize: bool

    def describe(self) -> dict:
        return {
            "members": [member.describe(self.normalize) for member in self.members],
            "total_objective": self.total_objective,
            "normalize": self.normalize,
            "search": {
                "exact": self.exact,
                "iterations": self.iterations,
                "conflicts": self.conflicts,
                "unused_count": len(self.unused),
            },
            "unused": [
                {"id": piece.artifact_id, "slot": piece.artifact.slot, "set_id": piece.artifact.set_id}
                for piece in self.unused
            ],
            "warnings": list(self.warnings),
        }


def _identity(piece: LoadoutPiece) -> str:
    return str(piece.artifact_id) if piece.artifact_id is not None else f"object:{id(piece.artifact)}"


def _selected(loadout: Loadout) -> list[LoadoutPiece]:
    return [piece for piece in loadout.pieces if piece is not None]


def _objective(assignments: Sequence[PartyLoadout], normalize: bool) -> float:
    return sum(member.member.weight * member.score(normalize) for member in assignments)


def _valid(loadout: Loadout, target: int | None) -> bool:
    return target is None or f"SET_{target}_4" in loadout.set_flags


def _validate_inventory(pieces: Sequence[LoadoutPiece], members: Sequence[PartyMember]) -> None:
    seen = set()
    for piece in pieces:
        key = _identity(piece)
        if key in seen:
            raise ValueError(f"库存中存在重复圣遗物标识：{key}")
        seen.add(key)
    for slot in _SLOTS:
        count = sum(1 for piece in pieces if piece.artifact.slot == slot)
        if count < len(members):
            raise ValueError(f"{slot} 部位只有 {count} 件圣遗物，不足以装备 {len(members)} 个角色")
    requirements: dict[int, int] = {}
    for member in members:
        target = member.resolved_set()
        if target is not None:
            requirements[target] = requirements.get(target, 0) + 1
    for target, users in requirements.items():
        count = sum(1 for piece in pieces if piece.artifact.set_id == target)
        if count < 4 * users:
            raise ValueError(
                f"套装「{SETS[target]['name']}」只有 {count} 件，"
                f"不足以让 {users} 个角色各凑齐四件套",
            )


def _greedy(
    pieces: Sequence[LoadoutPiece],
    members: Sequence[PartyMember],
    panels: Sequence[dict],
    targets: Sequence[int | None],
    references: Sequence[float],
    order: Sequence[int],
    max_nodes: int,
) -> tuple[list[PartyLoadout], list[LoadoutPiece], int] | None:
    available = list(pieces)
    assignments: list[PartyLoadout] = [None] * len(members)  # type: ignore[list-item]
    nodes = 0
    for index in order:
        member = members[index]
        try:
            result = optimize_loadout(
                available,
                member.model,
                panels[index],
                required_four_piece_set=targets[index],
                max_nodes=max_nodes,
            )
        except ValueError:
            return None
        nodes += result.nodes
        selected = _selected(result.loadout)
        used = {_identity(piece) for piece in selected}
        available = [piece for piece in available if _identity(piece) not in used]
        assignments[index] = PartyLoadout(
            member=member,
            loadout=result.loadout,
            reference_damage=references[index],
            target_four_piece_set=targets[index],
        )
    return assignments, available, nodes


def _orders(count: int, restarts: int, seed: int) -> list[tuple[int, ...]]:
    base = tuple(range(count))
    candidates = [base, tuple(reversed(base))]
    rng = random.Random(seed)
    for _ in range(max(0, restarts)):
        order = list(base)
        rng.shuffle(order)
        candidates.append(tuple(order))
    unique = []
    seen = set()
    for order in candidates:
        if order not in seen:
            seen.add(order)
            unique.append(order)
    return unique


def _replace_slot(pieces: Sequence[LoadoutPiece | None], slot_index: int,
                  piece: LoadoutPiece | None) -> list[LoadoutPiece | None]:
    result = list(pieces)
    result[slot_index] = piece
    return result


def _improve(
    assignments: list[PartyLoadout],
    unused: list[LoadoutPiece],
    members: Sequence[PartyMember],
    panels: Sequence[dict],
    targets: Sequence[int | None],
    *,
    normalize: bool,
    max_nodes: int,
    max_iterations: int,
) -> tuple[list[PartyLoadout], list[LoadoutPiece], int, bool]:
    iterations = 0
    hit_limit = False
    for _ in range(max_iterations):
        iterations += 1
        before = _objective(assignments, normalize)

        # Coordinate ascent: re-optimize one member from unused plus its own pieces.
        for index, member in enumerate(members):
            own = _selected(assignments[index].loadout)
            pool = list(unused) + own
            try:
                result = optimize_loadout(
                    pool, member.model, panels[index],
                    required_four_piece_set=targets[index], max_nodes=max_nodes,
                )
            except ValueError:
                continue
            if not result.exact:
                hit_limit = True
            candidate = PartyLoadout(
                member=member,
                loadout=result.loadout,
                reference_damage=assignments[index].reference_damage,
                target_four_piece_set=targets[index],
            )
            trial = list(assignments)
            trial[index] = candidate
            if _objective(trial, normalize) > before + 1e-12:
                assignments = trial
                used = {_identity(piece) for assignment in assignments for piece in _selected(assignment.loadout)}
                unused = [piece for piece in pool if _identity(piece) not in used]
                before = _objective(assignments, normalize)

        # Same-slot swaps across members.
        improved = True
        while improved:
            improved = False
            for left in range(len(members)):
                for right in range(left + 1, len(members)):
                    old_pair = (
                        members[left].weight * assignments[left].score(normalize)
                        + members[right].weight * assignments[right].score(normalize)
                    )
                    for slot_index in range(len(_SLOTS)):
                        left_piece = assignments[left].loadout.pieces[slot_index]
                        right_piece = assignments[right].loadout.pieces[slot_index]
                        if left_piece is None or right_piece is None:
                            continue
                        if _identity(left_piece) == _identity(right_piece):
                            continue
                        left_trial = evaluate_loadout(
                            _replace_slot(assignments[left].loadout.pieces, slot_index, right_piece),
                            members[left].model, panels[left],
                        )
                        right_trial = evaluate_loadout(
                            _replace_slot(assignments[right].loadout.pieces, slot_index, left_piece),
                            members[right].model, panels[right],
                        )
                        if not _valid(left_trial, targets[left]) or not _valid(right_trial, targets[right]):
                            continue
                        new_pair = (
                            members[left].weight * (
                                left_trial.damage / assignments[left].reference_damage
                                if normalize and assignments[left].reference_damage > 0 else left_trial.damage
                            )
                            + members[right].weight * (
                                right_trial.damage / assignments[right].reference_damage
                                if normalize and assignments[right].reference_damage > 0 else right_trial.damage
                            )
                        )
                        if new_pair <= old_pair + 1e-12:
                            continue
                        assignments[left] = PartyLoadout(
                            member=members[left], loadout=left_trial,
                            reference_damage=assignments[left].reference_damage,
                            target_four_piece_set=targets[left],
                        )
                        assignments[right] = PartyLoadout(
                            member=members[right], loadout=right_trial,
                            reference_damage=assignments[right].reference_damage,
                            target_four_piece_set=targets[right],
                        )
                        improved = True
                        break
                    if improved:
                        break
                if improved:
                    break

        used = {_identity(piece) for assignment in assignments for piece in _selected(assignment.loadout)}
        all_pieces = list(unused) + [piece for assignment in assignments for piece in _selected(assignment.loadout)]
        unused = [piece for piece in all_pieces if _identity(piece) not in used]
        after = _objective(assignments, normalize)
        if not improved and after <= before + 1e-12:
            break
    return assignments, unused, iterations, hit_limit


def optimize_party(
    pieces: Sequence[LoadoutPiece | object],
    members: Sequence[PartyMember],
    *,
    max_nodes: int = 2_000_000,
    restarts: int = 4,
    seed: int = 0,
    max_iterations: int = 8,
    normalize: bool = True,
) -> PartyOptimizeResult:
    """Assign one five-piece loadout to every member without sharing artifacts."""
    if not members:
        raise ValueError("至少需要一个角色")
    from data.references import is_manekin_identity, is_traveler_identity
    traveler_members = [
        member for member in members
        if is_traveler_identity(member.model.character)
        or is_traveler_identity(member.character.get("id"))
    ]
    if len(traveler_members) > 1:
        raise ValueError("一个队伍只能包含一个旅行者身份；空、荧及不同元素形态互斥")
    manekin_members = [
        member for member in members
        if is_manekin_identity(member.model.character)
        or is_manekin_identity(member.character.get("id"))
    ]
    if len(manekin_members) > 1:
        raise ValueError("一个队伍只能包含一个奇偶身份；男性与女性外观互斥")
    if manekin_members:
        raise ValueError("奇偶战斗模型尚未校准，不参与配装评分")
    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_nodes 必须是正整数")
    if type(restarts) is not int or restarts < 0:
        raise ValueError("restarts 必须是非负整数")
    if type(max_iterations) is not int or max_iterations < 1:
        raise ValueError("max_iterations 必须是正整数")
    normalized_pieces = []
    for item in pieces:
        if isinstance(item, LoadoutPiece):
            normalized_pieces.append(item)
        else:
            from core.models import Artifact
            if isinstance(item, Artifact):
                normalized_pieces.append(LoadoutPiece(item))
            else:
                raise TypeError("库存只能包含 Artifact 或 LoadoutPiece")
    panels = [member.panel() for member in members]
    targets = [member.resolved_set() for member in members]
    _validate_inventory(normalized_pieces, members)

    independent: list[PartyLoadout] = []
    total_nodes = 0
    independent_exact = True
    for index, member in enumerate(members):
        result = optimize_loadout(
            normalized_pieces, member.model, panels[index],
            required_four_piece_set=targets[index], max_nodes=max_nodes,
        )
        total_nodes += result.nodes
        independent_exact = independent_exact and result.exact
        independent.append(PartyLoadout(
            member=member,
            loadout=result.loadout,
            reference_damage=result.loadout.damage,
            target_four_piece_set=targets[index],
        ))

    usage: dict[str, int] = {}
    for assignment in independent:
        for piece in _selected(assignment.loadout):
            usage[_identity(piece)] = usage.get(_identity(piece), 0) + 1
    conflicts = sum(count - 1 for count in usage.values() if count > 1)

    warnings: list[str] = []
    for member in members:
        calibration = member.model.assumptions.get("calibration")
        if isinstance(calibration, dict) and calibration.get("accuracy") in ("low", "unvalidated"):
            warnings.append(
                f"{member.label} 的模型标定误差较高（{calibration.get('accuracy')}），结果仅作参考。",
            )
        if member.weapon is None and member.model.assumptions.get("weapon") is None:
            warnings.append(f"{member.label} 未提供武器；模型只使用角色白值，武器特效未计入。")
        if (
            member.fixed_four_piece_set is not None
            and member.fixed_four_piece_set != member.model.target_four_piece_set
        ):
            target_name = SETS[member.fixed_four_piece_set]["name"]
            model_set = member.model.target_four_piece_set
            model_name = SETS[model_set]["name"] if model_set in SETS else "无"
            warnings.append(
                f"{member.label} 固定为「{target_name}」四件套，但伤害模型覆盖的是「{model_name}」；"
                "未被该模型覆盖的条件套装效果不会计入目标值。",
            )
    if conflicts == 0:
        used = set(usage)
        unused = [piece for piece in normalized_pieces if _identity(piece) not in used]
        return PartyOptimizeResult(
            members=tuple(independent),
            total_objective=_objective(independent, normalize),
            exact=independent_exact,
            iterations=0,
            conflicts=0,
            unused=tuple(unused),
            warnings=tuple(warnings),
            normalize=normalize,
        )

    warnings.append("库存存在角色争用；联合配装使用启发式搜索，不保证全局最优。")
    references = [assignment.reference_damage for assignment in independent]
    best = None
    for order in _orders(len(members), restarts, seed):
        greedy = _greedy(normalized_pieces, members, panels, targets, references, order, max_nodes)
        if greedy is None:
            continue
        assignments, unused, nodes = greedy
        total_nodes += nodes
        score = _objective(assignments, normalize)
        if best is None or score > best[0]:
            best = (score, assignments, unused)
    if best is None:
        raise ValueError("在目标四件套约束下无法为所有角色同时配装；请增加库存或减少角色")

    assignments, unused, iterations, hit_limit = _improve(
        list(best[1]), list(best[2]), members, panels, targets,
        normalize=normalize, max_nodes=max_nodes, max_iterations=max_iterations,
    )
    if hit_limit:
        warnings.append("部分配装搜索达到节点上限，结果可能不是该步骤的最优解。")
    selected_pieces = [piece for assignment in assignments for piece in _selected(assignment.loadout)]
    if any(piece.artifact.level < 20 for piece in selected_pieces):
        warnings.append("联合配装包含未满级圣遗物；升级已有圣遗物是另一类提升。")

    used = {_identity(piece) for piece in selected_pieces}
    unused = [piece for piece in normalized_pieces if _identity(piece) not in used]
    return PartyOptimizeResult(
        members=tuple(assignments),
        total_objective=_objective(assignments, normalize),
        exact=False,
        iterations=iterations,
        conflicts=conflicts,
        unused=tuple(unused),
        warnings=tuple(warnings),
        normalize=normalize,
    )
