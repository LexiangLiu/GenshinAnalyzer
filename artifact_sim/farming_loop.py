"""Iterative domain farming on top of weapon + artifact optimization.

Each round ranks candidate domains for the current builds, simulates a batch
of five-star drops from the chosen domain, adds them to the inventory, and
re-runs the two-dimensional weapon + artifact optimizer.  Repeating this shows
how the recommended loadouts and the next best domain evolve as the account is
farmed.

Only five-star drops are modeled.  Each drop is treated as one 20-resin claim,
matching the simplification used by the rest of the package.  Weapon passives
remain the fixed approximations carried by :class:`WeaponOption`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Sequence

from .damage_models import DamageModel
from .game_data import DOMAINS, lookup
from .inventory import Inventory
from .loadout_optimizer import Loadout, LoadoutPiece
from .models import Artifact
from .party_optimizer import PartyMember
from .party_plan import compact_main_stats, rank_domains_for_loadouts
from .simulation import Simulator
from .weapon_optimizer import (
    WeaponChoice, WeaponMember, WeaponOption, evaluate_member_weapon,
    optimize_weapons_and_artifacts,
)


@dataclass(frozen=True)
class FarmRound:
    """One recommend -> farm -> re-optimize cycle."""

    index: int
    domain_id: int
    domain_name: str
    before_damage: float
    after_damage: float
    drops: tuple[dict, ...]
    new_artifact_ids: tuple[str, ...]
    used_new_artifact_ids: tuple[str, ...]
    choices: tuple[WeaponChoice, ...]
    domain_ranking: tuple[dict, ...]
    recommended_domain_id: int | None
    warnings: tuple[str, ...]

    def describe(self, baseline_damage: float | None) -> dict:
        def gain(reference: float | None) -> float | None:
            if reference is None or reference <= 0:
                return None
            return (self.after_damage / reference - 1.0) * 100.0

        used = set(self.used_new_artifact_ids)
        return {
            "round": self.index,
            "domain": {"id": self.domain_id, "name": self.domain_name},
            "before_damage": self.before_damage,
            "after_damage": self.after_damage,
            "gain_pct_vs_baseline": gain(baseline_damage),
            "gain_pct_vs_previous": gain(self.before_damage),
            "drops": list(self.drops),
            "used_drops": [row for row in self.drops if row["id"] in used],
            "weapon_assignment": {
                choice.member.label: choice.weapon.name for choice in self.choices
            },
            "members": [choice.describe() for choice in self.choices],
            "domain_ranking": list(self.domain_ranking),
            "recommended_domain_id": self.recommended_domain_id,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class FarmingLoopResult:
    baseline_damage: float
    rounds: tuple[FarmRound, ...]
    final_choices: tuple[WeaponChoice, ...]
    final_damage: float
    final_domain_ranking: tuple[dict, ...]
    recommended_domain_id: int | None
    total_drops: int
    total_resin: int
    stopped_reason: str
    warnings: tuple[str, ...]

    def describe(self) -> dict:
        final_gain = None
        if self.baseline_damage > 0:
            final_gain = (self.final_damage / self.baseline_damage - 1.0) * 100.0
        return {
            "baseline_damage": self.baseline_damage,
            "rounds": [row.describe(self.baseline_damage) for row in self.rounds],
            "final": {
                "damage": self.final_damage,
                "gain_pct_vs_baseline": final_gain,
                "weapon_assignment": {
                    choice.member.label: choice.weapon.name for choice in self.final_choices
                },
                "members": [choice.describe() for choice in self.final_choices],
                "recommended_domain_id": self.recommended_domain_id,
                "domain_ranking": list(self.final_domain_ranking),
            },
            "total_drops": self.total_drops,
            "total_resin": self.total_resin,
            "stopped_reason": self.stopped_reason,
            "warnings": list(self.warnings),
        }


def _describe_drop(artifact_id: str, artifact: Artifact) -> dict:
    data = artifact.describe()
    return {
        "id": artifact_id,
        "set_id": artifact.set_id,
        "set_name": data["set_name"],
        "piece_name": data["piece_name"],
        "slot": artifact.slot,
        "main_stat": artifact.main_stat,
        "main_value": artifact.main_value,
        "level": artifact.level,
        "substats": data["active_substats"],
        "score": data["score"],
    }


def _party_member(choice: WeaponChoice) -> PartyMember:
    return PartyMember(
        model=choice.model,
        character=choice.member.character,
        weapon=choice.weapon.as_panel(),
        weight=choice.member.weight,
        target_four_piece_set=choice.member.resolved_set(),
        name=choice.member.label,
    )


def _current_choice(
    member: WeaponMember,
    weapon: WeaponOption,
    loadout: Loadout,
    model: DamageModel,
) -> WeaponChoice:
    return WeaponChoice(
        member=member,
        weapon=weapon,
        model=model,
        loadout=loadout,
        damage=loadout.damage,
        independent_damage=loadout.damage,
        current_damage=loadout.damage,
        current_weapon=weapon,
        weapon_scores=(),
    )


def _main_stats_for(
    members: Sequence[PartyMember],
    main_stats: Sequence[dict | None] | None,
) -> Sequence[dict | None]:
    if main_stats is not None:
        return main_stats
    return [compact_main_stats(member.model) for member in members]


def simulate_farming_loop(
    inventory: Inventory,
    members: Sequence[WeaponMember],
    *,
    rounds: int = 3,
    drops_per_round: int = 8,
    domains: Sequence[str | int] | None = None,
    fixed_domain: str | int | None = None,
    enhance_to: int = 20,
    max_nodes: int = 30_000,
    restarts: int = 1,
    max_iterations: int = 1,
    seed: int = 0,
    normalize: bool = False,
    threshold_pct: float = 1.0,
    resin_per_claim: int = 20,
    score_mode: str = "sum",
    target_gain_pct: float | None = None,
    main_stats: Sequence[dict | None] | None = None,
    equip_best: bool = False,
) -> FarmingLoopResult:
    """Recommend a domain, simulate drops, and re-optimize for several rounds.

    ``members`` carry the pre-farming weapons and loadouts.  When every member
    has them, the first recommendation is based on that current build;
    otherwise an initial joint optimization supplies the starting point.

    New artifacts are added to ``inventory``.  Set ``equip_best`` to also move
    each round's optimized artifacts onto the member profiles in the database.
    """
    if not members:
        raise ValueError("至少需要一个角色")
    if type(rounds) is not int or rounds < 1:
        raise ValueError("rounds 必须是正整数")
    if type(drops_per_round) is not int or drops_per_round < 1:
        raise ValueError("drops_per_round 必须是正整数")
    if type(enhance_to) is not int or not 0 <= enhance_to <= 20:
        raise ValueError("enhance_to 必须是 0 到 20 的整数")
    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_nodes 必须是正整数")
    if type(restarts) is not int or restarts < 0:
        raise ValueError("restarts 必须是非负整数")
    if type(max_iterations) is not int or max_iterations < 1:
        raise ValueError("max_iterations 必须是正整数")
    if type(resin_per_claim) is not int or resin_per_claim <= 0:
        raise ValueError("resin_per_claim 必须是正整数")
    if type(threshold_pct) not in (int, float) or not isfinite(threshold_pct) or threshold_pct < 0:
        raise ValueError("threshold_pct 必须是有限的非负数")
    if score_mode not in ("sum", "max"):
        raise ValueError("score_mode 只能是 sum 或 max")
    if target_gain_pct is not None and (
        type(target_gain_pct) not in (int, float)
        or not isfinite(target_gain_pct)
        or target_gain_pct <= 0
    ):
        raise ValueError("target_gain_pct 必须是有限正数")
    if main_stats is not None and len(main_stats) != len(members):
        raise ValueError("main_stats 必须与角色数量一致")

    fixed_id = int(lookup(DOMAINS, fixed_domain)["id"]) if fixed_domain is not None else None
    domain_query = list(domains) if domains is not None else None
    if fixed_id is not None:
        if domain_query is None:
            domain_query = [fixed_id]
        else:
            known = {int(lookup(DOMAINS, item)["id"]) for item in domain_query}
            if fixed_id not in known:
                domain_query.append(fixed_id)

    pieces = [
        LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
        for artifact_id, artifact in inventory.artifacts()
    ]
    if not pieces:
        raise ValueError("库存中没有可用于刷取模拟的圣遗物")

    warnings: list[str] = []
    has_current = all(
        member.current_weapon is not None and member.current_pieces
        for member in members
    )
    if has_current:
        baseline = 0.0
        baseline_by_label = {}
        starting_members = []
        starting_loadouts = []
        current_choices = []
        for member in members:
            weapon = member.current_weapon
            loadout, model = evaluate_member_weapon(
                member, weapon, list(member.current_pieces or ()),
            )
            baseline += loadout.damage
            baseline_by_label[member.label] = loadout.damage
            starting_members.append(PartyMember(
                model=model,
                character=member.character,
                weapon=weapon.as_panel(),
                weight=member.weight,
                target_four_piece_set=member.resolved_set(),
                name=member.label,
            ))
            starting_loadouts.append(list(loadout.artifacts()))
            current_choices.append(_current_choice(member, weapon, loadout, model))
        final_choices = tuple(current_choices)
    else:
        initial = optimize_weapons_and_artifacts(
            pieces, members,
            max_nodes=max_nodes, restarts=restarts, max_iterations=max_iterations,
            normalize=normalize, seed=seed,
        )
        baseline = float(sum(choice.damage for choice in initial.members))
        baseline_by_label = {choice.member.label: choice.damage for choice in initial.members}
        starting_members = [_party_member(choice) for choice in initial.members]
        starting_loadouts = [list(choice.loadout.artifacts()) for choice in initial.members]
        final_choices = tuple(
            replace(choice, current_damage=choice.damage) for choice in initial.members
        )
        warnings.extend(initial.warnings)
        warnings.append("部分角色缺少当前武器或配装，基线使用初始联合最优。")

    simulator = Simulator(seed)
    round_rows: list[FarmRound] = []
    previous_damage = baseline
    final_ranking: tuple[dict, ...] = ()
    final_recommended: int | None = None
    stopped_reason = "round_limit"

    for index in range(1, rounds + 1):
        ranking, recommended, rank_warnings = rank_domains_for_loadouts(
            starting_members,
            starting_loadouts,
            [member.target_four_piece_set for member in starting_members],
            domain_query,
            main_stats=_main_stats_for(starting_members, main_stats),
            threshold_pct=threshold_pct,
            resin_per_claim=resin_per_claim,
            score_mode=score_mode,
        )
        warnings.extend(rank_warnings)
        final_ranking = tuple(ranking)
        final_recommended = recommended

        domain_id = fixed_id if fixed_id is not None else recommended
        if domain_id is None:
            stopped_reason = "no_positive_domain"
            warnings.append("没有可提升队伍的秘境，停止刷取。")
            break

        new_artifacts = []
        for _ in range(drops_per_round):
            artifact = simulator.drop(domain_id)
            if enhance_to:
                artifact = simulator.enhance(artifact, enhance_to)
            new_artifacts.append(artifact)

        new_ids = inventory.add(new_artifacts)
        new_pieces = [
            LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
            for artifact_id, artifact in zip(new_ids, new_artifacts)
        ]
        pieces.extend(new_pieces)

        optimized = optimize_weapons_and_artifacts(
            pieces, members,
            max_nodes=max_nodes, restarts=restarts, max_iterations=max_iterations,
            normalize=normalize, seed=seed + index,
        )
        choices = tuple(
            replace(choice, current_damage=baseline_by_label.get(choice.member.label))
            for choice in optimized.members
        )
        warnings.extend(optimized.warnings)
        after_damage = float(sum(choice.damage for choice in choices))

        equipped_ids = {
            piece.artifact_id for choice in choices
            for piece in choice.loadout.pieces if piece is not None
        }
        used_ids = tuple(artifact_id for artifact_id in new_ids if artifact_id in equipped_ids)
        drop_rows = tuple(
            _describe_drop(artifact_id, artifact)
            for artifact_id, artifact in zip(new_ids, new_artifacts)
        )
        round_rows.append(FarmRound(
            index=index,
            domain_id=domain_id,
            domain_name=DOMAINS[domain_id]["name"],
            before_damage=previous_damage,
            after_damage=after_damage,
            drops=drop_rows,
            new_artifact_ids=tuple(new_ids),
            used_new_artifact_ids=used_ids,
            choices=tuple(choices),
            domain_ranking=tuple(ranking),
            recommended_domain_id=recommended,
            warnings=tuple(optimized.warnings),
        ))

        previous_damage = after_damage
        final_choices = tuple(choices)
        starting_members = [_party_member(choice) for choice in choices]
        starting_loadouts = [list(choice.loadout.artifacts()) for choice in choices]

        if equip_best:
            for choice in choices:
                character = str(
                    choice.member.character.get("id") or choice.member.base_model.character,
                )
                artifact_ids = [
                    piece.artifact_id for piece in choice.loadout.pieces
                    if piece is not None
                ]
                inventory.equip(character, artifact_ids)

        if target_gain_pct is not None and baseline > 0:
            gain = (after_damage / baseline - 1.0) * 100.0
            if gain >= target_gain_pct:
                stopped_reason = "target_gain_reached"
                break

    # Recommendation for the next farming step after the final loadouts.
    if round_rows:
        ranking, recommended, rank_warnings = rank_domains_for_loadouts(
            starting_members,
            starting_loadouts,
            [member.target_four_piece_set for member in starting_members],
            domain_query,
            main_stats=_main_stats_for(starting_members, main_stats),
            threshold_pct=threshold_pct,
            resin_per_claim=resin_per_claim,
            score_mode=score_mode,
        )
        warnings.extend(rank_warnings)
        final_ranking = tuple(ranking)
        final_recommended = recommended

    total_drops = sum(len(row.drops) for row in round_rows)
    if total_drops:
        warnings.append(
            f"每件五星掉落按 {resin_per_claim} 树脂结算，并强化至 +{enhance_to}。",
        )
        if enhance_to == 20:
            warnings.append("模拟假定所有掉落都强化到 +20；真实账号只会强化有潜力的胚子。")

    return FarmingLoopResult(
        baseline_damage=baseline,
        rounds=tuple(round_rows),
        final_choices=final_choices,
        final_damage=float(sum(choice.damage for choice in final_choices)),
        final_domain_ranking=final_ranking,
        recommended_domain_id=final_recommended,
        total_drops=total_drops,
        total_resin=total_drops * resin_per_claim,
        stopped_reason=stopped_reason,
        warnings=tuple(dict.fromkeys(warnings)),
    )
