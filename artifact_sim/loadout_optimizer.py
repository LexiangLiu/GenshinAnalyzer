"""Exact artifact loadout search for a single-character damage model."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from math import inf
from typing import Mapping, Sequence

from .build_objective import as_decimal
from .damage_models import DamageModel
from .equipment import set_effects
from .game_data import SETS, lookup
from .models import Artifact


_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")


@dataclass(frozen=True)
class LoadoutPiece:
    artifact: Artifact
    artifact_id: str | None = None


@dataclass(frozen=True)
class Loadout:
    pieces: tuple[LoadoutPiece | None, ...]
    stats: Mapping[str, Decimal]
    set_counts: Mapping[int, int]
    set_flags: frozenset[str]
    set_bonuses: tuple[dict, ...]
    damage: float

    def artifacts(self) -> tuple[Artifact, ...]:
        return tuple(piece.artifact for piece in self.pieces if piece is not None)

    def describe(self) -> dict:
        panel = {key: float(value) for key, value in sorted(self.stats.items())}
        pieces = []
        for slot, piece in zip(_SLOTS, self.pieces):
            if piece is None:
                pieces.append({"slot": slot, "id": None, "artifact": None})
                continue
            pieces.append({
                "slot": slot,
                "id": piece.artifact_id,
                **piece.artifact.describe(),
            })
        counts = {str(key): value for key, value in sorted(self.set_counts.items())}
        return {
            "damage": self.damage,
            "pieces": pieces,
            "set_counts": counts,
            "set_names": {str(key): SETS[key]["name"] for key in self.set_counts},
            "set_flags": sorted(self.set_flags),
            "active_set_bonuses": list(self.set_bonuses),
            "panel_stats": panel,
            "final_panel": _final_panel(self.stats),
        }


@dataclass(frozen=True)
class OptimizeResult:
    loadout: Loadout
    nodes: int
    exact: bool
    required_four_piece_set: int | None
    max_nodes: int

    def describe(self) -> dict:
        return {
            "loadout": self.loadout.describe(),
            "search": {
                "nodes": self.nodes,
                "exact": self.exact,
                "max_nodes": self.max_nodes,
                "required_four_piece_set": self.required_four_piece_set,
            },
        }


@lru_cache(maxsize=8192)
def _artifact_contribution(artifact: Artifact) -> tuple[tuple[str, Decimal], ...]:
    stats = {artifact.main_stat: as_decimal(artifact.main_value)}
    for stat, value in artifact.substats().items():
        stats[stat] = stats.get(stat, Decimal(0)) + as_decimal(value)
    return tuple(sorted(stats.items()))


@lru_cache(maxsize=1024)
def _set_effects_cached(items: tuple[tuple[int, int], ...]):
    static, bonuses = set_effects(dict(items))
    return tuple(sorted(static.items())), tuple(bonuses)


def _normalize_base(base_stats: Mapping[str, object]) -> dict[str, Decimal]:
    return {
        key: value if isinstance(value, Decimal) else as_decimal(value)
        for key, value in base_stats.items()
    }


def _final_panel(stats: Mapping[str, Decimal]) -> dict:
    panel = {}
    for core in ("HP", "ATK", "DEF"):
        base = stats.get(f"BASE_{core}", Decimal(0))
        percent = stats.get(f"{core}_PERCENT", Decimal(0))
        flat = stats.get(core, Decimal(0))
        panel[core] = float(base * (1 + percent / 100) + flat)
    for stat in ("CRIT_RATE", "CRIT_DMG", "ENERGY_RECHARGE", "ELEMENTAL_MASTERY", "HYDRO_DMG_BONUS"):
        if stat in stats:
            panel[stat] = float(stats[stat])
    return panel


def _normalize_pieces(pieces: Sequence[LoadoutPiece | Artifact | None]) -> list[LoadoutPiece | None]:
    result = []
    for item in pieces:
        if item is None:
            result.append(None)
        elif isinstance(item, LoadoutPiece):
            result.append(item)
        elif isinstance(item, Artifact):
            result.append(LoadoutPiece(item))
        else:
            raise TypeError("配装只能包含 Artifact、LoadoutPiece 或 None")
    return result


def evaluate_loadout(
    pieces: Sequence[LoadoutPiece | Artifact | None],
    model: DamageModel,
    base_stats: Mapping[str, object],
) -> Loadout:
    """Evaluate one fixed loadout without searching."""
    by_slot = {}
    for piece in _normalize_pieces(pieces):
        if piece is None:
            continue
        slot = piece.artifact.slot
        if slot not in _SLOTS:
            raise ValueError(f"未知的圣遗物部位：{slot}")
        if slot in by_slot:
            raise ValueError("同一配装不能装备两个同部位圣遗物")
        by_slot[slot] = piece
    ordered = tuple(by_slot.get(slot) for slot in _SLOTS)
    stats = _normalize_base(base_stats)
    counts: dict[int, int] = {}
    for piece in ordered:
        if piece is None:
            continue
        for stat, value in _artifact_contribution(piece.artifact):
            stats[stat] = stats.get(stat, Decimal(0)) + value
        counts[piece.artifact.set_id] = counts.get(piece.artifact.set_id, 0) + 1
    static, bonuses = _set_effects_cached(tuple(sorted(counts.items())))
    for stat, value in static:
        stats[stat] = stats.get(stat, Decimal(0)) + value
    flags = frozenset(f"SET_{bonus['set_id']}_{bonus['pieces']}" for bonus in bonuses)
    return Loadout(ordered, stats, counts, flags, bonuses, model.evaluate(stats, flags))


def _max_static_bonus(relevant: frozenset[str], target_id: int | None) -> dict[str, Decimal]:
    """Component-wise static set-bonus upper bound for pruning.

    With a required four-piece target set, the target set's own two- and
    four-piece static bonuses are the only set bonuses that can be active.
    Otherwise at most one four-piece set or two two-piece sets fit in five
    slots; taking the maximum of those two shapes is a safe upper bound.
    """
    if target_id is not None:
        static, _ = set_effects({target_id: 4})
        return {stat: static.get(stat, Decimal(0)) for stat in relevant}
    two_piece: dict[str, list[Decimal]] = {stat: [] for stat in relevant}
    four_piece: dict[str, Decimal] = {stat: Decimal(0) for stat in relevant}
    for set_id, info in SETS.items():
        if 5 not in info["rarities"]:
            continue
        static2, _ = set_effects({set_id: 2})
        static4, _ = set_effects({set_id: 4})
        for stat in relevant:
            value2 = static2.get(stat, Decimal(0))
            value4 = static4.get(stat, Decimal(0))
            if value2:
                two_piece[stat].append(value2)
            if value4 > four_piece[stat]:
                four_piece[stat] = value4
    result = {}
    for stat in relevant:
        top = sorted(two_piece[stat], reverse=True)[:2]
        result[stat] = max(four_piece[stat], sum(top, Decimal(0)))
    return result


def _pareto_rows(rows, relevant, monotone):
    """Remove artifacts dominated in every relevant stat, within one set.

    Dominance is only used inside a set group, so it cannot hide set-count
    effects. For the target-set slots all candidates belong to the same set;
    for an off-piece slot the groups are still kept separate.
    """
    if not monotone:
        return rows
    groups = {}
    for piece, contribution, set_id in rows:
        groups.setdefault(set_id, []).append((piece, contribution, set_id))
    result = []
    for group in groups.values():
        unique = {}
        for piece, contribution, set_id in group:
            key = tuple(sorted(contribution.items()))
            unique.setdefault(key, (piece, contribution, set_id))
        items = list(unique.values())
        vectors = [tuple(contribution.get(stat, Decimal(0)) for stat in relevant)
                   for _, contribution, _ in items]
        for index, item in enumerate(items):
            vector = vectors[index]
            dominated = False
            for other_index, other in enumerate(vectors):
                if index == other_index:
                    continue
                if (all(right >= left for left, right in zip(vector, other))
                        and any(right > left for left, right in zip(vector, other))):
                    dominated = True
                    break
            if not dominated:
                result.append(item)
    return result


def _search_case(candidate_map, base, relevant, target_id, model, max_nodes):
    slot_max = {}
    for slot, rows in candidate_map.items():
        maximum = {stat: Decimal(0) for stat in relevant}
        for _, contribution, _ in rows:
            for stat in relevant:
                value = contribution.get(stat, Decimal(0))
                if value > maximum[stat]:
                    maximum[stat] = value
        slot_max[slot] = maximum
    max_static = _max_static_bonus(frozenset(relevant), target_id)
    upper_flags = model.set_flags
    order = tuple(sorted(_SLOTS, key=lambda slot: len(candidate_map[slot])))
    nodes = 0
    exact = True
    best_damage = -inf
    best_selected = None

    def upper_bound(index: int, stats: dict[str, Decimal]) -> float:
        if not model.monotone:
            return inf
        upper = stats.copy()
        for remaining_slot in order[index:]:
            for stat, value in slot_max[remaining_slot].items():
                upper[stat] = upper.get(stat, Decimal(0)) + value
        for stat, value in max_static.items():
            upper[stat] = upper.get(stat, Decimal(0)) + value
        return model.evaluate(upper, upper_flags)

    def search(index: int, stats: dict[str, Decimal], counts: dict[int, int],
               selected: dict[str, LoadoutPiece | None]) -> None:
        nonlocal nodes, exact, best_damage, best_selected
        if not exact:
            return
        if nodes >= max_nodes:
            exact = False
            return
        nodes += 1
        if index == len(order):
            static, bonuses = _set_effects_cached(tuple(sorted(counts.items())))
            final_stats = stats.copy()
            for stat, value in static:
                final_stats[stat] = final_stats.get(stat, Decimal(0)) + value
            flags = frozenset(f"SET_{bonus['set_id']}_{bonus['pieces']}" for bonus in bonuses)
            damage = model.evaluate(final_stats, flags)
            if damage > best_damage:
                best_damage = damage
                best_selected = selected.copy()
            return
        slot = order[index]
        for piece, contribution, set_id in candidate_map[slot]:
            new_stats = stats.copy()
            for stat, value in contribution.items():
                new_stats[stat] = new_stats.get(stat, Decimal(0)) + value
            new_counts = counts.copy()
            if set_id is not None:
                new_counts[set_id] = new_counts.get(set_id, 0) + 1
            if model.monotone and upper_bound(index + 1, new_stats) <= best_damage:
                continue
            selected[slot] = piece
            search(index + 1, new_stats, new_counts, selected)
            del selected[slot]

    search(0, base, {}, {})
    return best_selected, best_damage, nodes, exact


def optimize_loadout(
    pieces: Sequence[LoadoutPiece | Artifact | None],
    model: DamageModel,
    base_stats: Mapping[str, object],
    *,
    required_four_piece_set: str | int | None = None,
    max_nodes: int = 2_000_000,
) -> OptimizeResult:
    """Find the highest-damage five-slot loadout under the given constraint.

    With a required four-piece set the search is decomposed into the five
    possible off-piece slots. Dominated artifacts are removed per set before
    the branch-and-bound search, which keeps larger simulated inventories
    practical. The result is exact when every case finishes within ``max_nodes``.
    """
    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_nodes 必须是正整数")
    target = None if required_four_piece_set is None else lookup(SETS, required_four_piece_set)
    if target is not None and (5 not in target["rarities"] or "4" not in target["effects"]):
        raise ValueError("required_four_piece_set 必须是有四件套效果的五星套装")
    target_id = None if target is None else target["id"]

    by_slot: dict[str, list[tuple[LoadoutPiece, dict[str, Decimal], int]]] = {
        slot: [] for slot in _SLOTS
    }
    for piece in _normalize_pieces(pieces):
        if piece is None:
            continue
        slot = piece.artifact.slot
        if slot not in by_slot:
            raise ValueError(f"未知的圣遗物部位：{slot}")
        by_slot[slot].append((piece, dict(_artifact_contribution(piece.artifact)), piece.artifact.set_id))
    empty = (None, {}, None)
    relevant = tuple(sorted(model.relevant_stats))
    base = _normalize_base(base_stats)
    total_nodes = 0
    exact = True
    best_selected = None
    best_damage = -inf

    if target_id is None:
        candidate_map = {
            slot: _pareto_rows(by_slot[slot], relevant, model.monotone) or [empty]
            for slot in _SLOTS
        }
        selected, damage, nodes, case_exact = _search_case(
            candidate_map, base, relevant, None, model, max_nodes,
        )
        best_selected, best_damage = selected, damage
        total_nodes += nodes
        exact = case_exact
    else:
        target_rows = {
            slot: [row for row in by_slot[slot] if row[2] == target_id]
            for slot in _SLOTS
        }
        target_front = {
            slot: _pareto_rows(target_rows[slot], relevant, model.monotone)
            for slot in _SLOTS
        }
        all_front = {
            slot: _pareto_rows(by_slot[slot], relevant, model.monotone) or [empty]
            for slot in _SLOTS
        }
        for off_slot in _SLOTS:
            if any(not target_front[slot] for slot in _SLOTS if slot != off_slot):
                continue
            candidate_map = {
                slot: (all_front[slot] if slot == off_slot else target_front[slot])
                for slot in _SLOTS
            }
            selected, damage, nodes, case_exact = _search_case(
                candidate_map, base, relevant, target_id, model, max_nodes,
            )
            total_nodes += nodes
            exact = exact and case_exact
            if selected is not None and damage > best_damage:
                best_selected, best_damage = selected, damage

    if best_selected is None:
        if target is not None:
            raise ValueError(f"库存中找不到可组成目标四件套「{target['name']}」的配装")
        raise ValueError("库存中找不到可组成完整配装的圣遗物")
    ordered = tuple(best_selected.get(slot) for slot in _SLOTS)
    loadout = evaluate_loadout(ordered, model, base_stats)
    return OptimizeResult(loadout, total_nodes, exact, target_id, max_nodes)
