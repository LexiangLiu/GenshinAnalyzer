"""Monte Carlo account-wide farming priorities over a shared artifact inventory."""

from __future__ import annotations

from dataclasses import replace
from math import fsum, isfinite, sqrt
from typing import Sequence

from core.models import Artifact
from core.simulation import Simulator
from data.game_data import DOMAINS, SETS, lookup
from optimization.loadout_optimizer import LoadoutPiece, evaluate_loadout
from optimization.party_optimizer import PartyMember, optimize_party
from .expectation import _estimate, _models, _panel_stats


DEFAULT_ACCOUNT_BUDGETS = (200, 600)
DEFAULT_ACCOUNT_SAMPLES = 12
MAX_ACCOUNT_SAMPLES = 64
MAX_ACCOUNT_DOMAINS = 8
MAX_ACCOUNT_BUDGETS = 8
MAX_ACCOUNT_SEARCHES = 192
ACCOUNT_SEARCH_NODES = 5_000
RESIN_PER_CLAIM = 20
_SEED_STRIDE = 104_729


def _quantile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _wilson_interval(successes: int, count: int) -> list[float] | None:
    if count == 0:
        return None
    z = 1.96
    proportion = successes / count
    denominator = 1 + z * z / count
    center = (proportion + z * z / (2 * count)) / denominator
    margin = z * sqrt(
        proportion * (1 - proportion) / count + z * z / (4 * count * count)
    ) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _snapshot(loadout, model, target_set: int | None) -> dict:
    formulas = {
        formula.key: float(formula.evaluate(loadout.stats, loadout.set_flags))
        for formula in _models(model)
    }
    return {
        "objective": float(loadout.damage),
        "panel": _panel_stats(loadout),
        "formulas": formulas,
        "target_set_complete": (
            None if target_set is None else loadout.set_counts.get(target_set, 0) >= 4
        ),
    }


def _objective_description(model) -> dict:
    describe = getattr(model, "describe_objective", None)
    if callable(describe):
        return describe()
    return {
        "mode": "single",
        "formulas": [{"key": model.key, "label": model.label, "weight": 1.0}],
    }


def _member_summary(
    member: PartyMember,
    role_weight: float,
    target_set: int | None,
    baseline: dict,
    observations: Sequence[dict],
    account_baseline_score: float,
    reference_damage: float,
) -> dict:
    damage_values = [row["objective"] for row in observations]
    damage_deltas = [value - baseline["objective"] for value in damage_values]
    damage_pct = [
        (delta / baseline["objective"] * 100) if baseline["objective"] > 0 else 0.0
        for delta in damage_deltas
    ]
    account_contributions = [
        role_weight * delta / reference_damage / account_baseline_score * 100
        for delta in damage_deltas
    ]
    panel_stats = sorted({
        stat for row in observations for stat in row["panel"]
    } | set(baseline["panel"]))
    formula_models = _models(member.model)
    objective = _objective_description(member.model)
    objective_formulas = objective.get("formulas", objective.get("weights", []))
    selected_formula_weights = {
        formula["key"]: float(formula.get("weight", 1.0))
        for formula in objective_formulas
    }
    return {
        "character": member.model.character,
        "name": member.label,
        "role_weight": role_weight,
        "model": member.model.key,
        "formula_objective": objective,
        "target_four_piece_set": (
            {"id": target_set, "name": SETS[target_set]["name"]}
            if target_set is not None else None
        ),
        "baseline": {
            "objective": baseline["objective"],
            "panel": baseline["panel"],
            "formulas": baseline["formulas"],
            "target_set_complete": baseline["target_set_complete"],
        },
        "expected_objective": _estimate(damage_values),
        "expected_objective_delta": _estimate(damage_deltas),
        "expected_gain_pct": _estimate(damage_pct),
        "account_gain_contribution_pct": _estimate(account_contributions),
        "panel": {
            stat: _estimate([row["panel"].get(stat, 0.0) for row in observations])
            for stat in panel_stats
        },
        "panel_delta": {
            stat: _estimate([
                row["panel"].get(stat, 0.0) - baseline["panel"].get(stat, 0.0)
                for row in observations
            ])
            for stat in panel_stats
        },
        "formulas": {
            formula.key: {
                "label": formula.label,
                "weight": selected_formula_weights.get(formula.key, 1.0),
                "damage": _estimate([
                    row["formulas"].get(formula.key, 0.0) for row in observations
                ]),
                "delta": _estimate([
                    row["formulas"].get(formula.key, 0.0)
                    - baseline["formulas"].get(formula.key, 0.0)
                    for row in observations
                ]),
            }
            for formula in formula_models
        },
        "target_set_completion_probability": (
            None
            if target_set is None
            else sum(bool(row["target_set_complete"]) for row in observations)
            / len(observations)
        ),
        "target_set_completion_ci95": (
            None
            if target_set is None
            else _wilson_interval(
                sum(bool(row["target_set_complete"]) for row in observations),
                len(observations),
            )
        ),
    }


def estimate_account_farming_priorities(
    pieces: Sequence[LoadoutPiece | Artifact],
    members: Sequence[PartyMember],
    *,
    domains: Sequence[str | int],
    resin_budgets: Sequence[int] = DEFAULT_ACCOUNT_BUDGETS,
    starting_inventory: str = "inventory",
    sample_count: int = DEFAULT_ACCOUNT_SAMPLES,
    target_gain_pct: float = 1.0,
    seed: int = 0,
) -> dict:
    """Estimate account-wide gains after farming each domain and re-optimizing.

    The account objective is a role-weighted sum of each member's selected
    formula score divided by its no-artifact score. Existing or simulated
    inventory is jointly re-assigned for every sample; target sets are soft
    from zero so incomplete accounts still have a measurable progression path.
    """
    if starting_inventory not in ("inventory", "zero"):
        raise ValueError("starting_inventory 必须是 inventory 或 zero")
    if not members:
        raise ValueError("至少需要一个目标角色")
    if (
        not isinstance(domains, (list, tuple))
        or not domains
        or len(domains) > MAX_ACCOUNT_DOMAINS
    ):
        raise ValueError(f"候选秘境需为 1–{MAX_ACCOUNT_DOMAINS} 个")
    domain_rows = []
    seen_domains = set()
    for domain in domains:
        info = lookup(DOMAINS, domain)
        domain_id = int(info["id"])
        if domain_id in seen_domains:
            raise ValueError("候选秘境不能重复")
        seen_domains.add(domain_id)
        domain_rows.append(info)
    if (
        not isinstance(resin_budgets, (list, tuple))
        or not resin_budgets
        or len(resin_budgets) > MAX_ACCOUNT_BUDGETS
    ):
        raise ValueError(f"树脂预算需包含 1–{MAX_ACCOUNT_BUDGETS} 档")
    budgets = []
    for budget in resin_budgets:
        if (
            type(budget) is not int
            or budget <= 0
            or budget > 4000
            or budget % RESIN_PER_CLAIM
        ):
            raise ValueError("每档树脂预算须为 20–4000 间的 20 倍数")
        budgets.append(budget)
    budgets = sorted(set(budgets))
    if len(budgets) != len(resin_budgets):
        raise ValueError("树脂预算不能重复")
    if type(sample_count) is not int or not 8 <= sample_count <= MAX_ACCOUNT_SAMPLES:
        raise ValueError(f"sample_count 必须介于 8 和 {MAX_ACCOUNT_SAMPLES} 之间")
    if (
        type(target_gain_pct) not in (int, float)
        or not isfinite(target_gain_pct)
        or not 0 <= target_gain_pct <= 100
    ):
        raise ValueError("达标阈值须为 0–100 间的有限百分数")
    if type(seed) is not int:
        raise ValueError("seed 必须是整数")
    search_count = len(domain_rows) * len(budgets) * sample_count
    if search_count > MAX_ACCOUNT_SEARCHES:
        raise ValueError(
            f"计算量过大；秘境数 × 预算档数 × 抽样次数不能超过 {MAX_ACCOUNT_SEARCHES}"
        )

    role_weight_total = sum(member.weight for member in members)
    role_weights = [member.weight / role_weight_total for member in members]
    reference_damage = [
        float(evaluate_loadout([], member.model, member.panel()).damage)
        for member in members
    ]
    if any(not isfinite(value) or value <= 0 for value in reference_damage):
        raise ValueError("角色裸面板模型目标值必须是有限正数")
    weighted_members = [
        replace(
            member,
            weight=role_weight / reference,
        )
        for member, role_weight, reference in zip(
            members, role_weights, reference_damage,
        )
    ]
    target_sets = [member.resolved_set() for member in members]
    starting_pieces = list(pieces) if starting_inventory == "inventory" else []
    baseline_result = optimize_party(
        starting_pieces,
        weighted_members,
        max_nodes=ACCOUNT_SEARCH_NODES,
        restarts=1,
        seed=seed,
        max_iterations=3,
        normalize=False,
        allow_partial=starting_inventory == "zero",
    )
    baseline_rows = [
        _snapshot(assignment.loadout, member.model, target)
        for assignment, member, target in zip(
            baseline_result.members, members, target_sets,
        )
    ]
    baseline_score = baseline_result.total_objective
    if not isfinite(baseline_score) or baseline_score <= 0:
        raise ValueError("账号基线目标值必须是有限正数")
    warnings = list(baseline_result.warnings)

    domain_results = []
    rankings_by_budget: dict[int, list[dict]] = {budget: [] for budget in budgets}
    for domain in domain_rows:
        rows_by_budget: dict[int, list[dict]] = {budget: [] for budget in budgets}
        exact_counts = {budget: 0 for budget in budgets}
        for sample in range(sample_count):
            simulator = Simulator(
                seed + int(domain["id"]) * 1_000_003 + sample * _SEED_STRIDE,
            )
            future: list[LoadoutPiece] = []
            current_claim = 0
            for budget in budgets:
                target_claim = budget // RESIN_PER_CLAIM
                while current_claim < target_claim:
                    drop = simulator.enhance(
                        simulator.drop(domain["id"]),
                        20,
                    )
                    future.append(LoadoutPiece(drop))
                    current_claim += 1
                inventory = [*starting_pieces, *future]
                result = optimize_party(
                    inventory,
                    weighted_members,
                    max_nodes=ACCOUNT_SEARCH_NODES,
                    restarts=1,
                    seed=seed + sample,
                    max_iterations=3,
                    normalize=False,
                    allow_partial=starting_inventory == "zero",
                )
                rows_by_budget[budget].append({
                    "score": result.total_objective,
                    "members": [
                        _snapshot(assignment.loadout, member.model, target)
                        for assignment, member, target in zip(
                            result.members, members, target_sets,
                        )
                    ],
                    "iterations": result.iterations,
                })
                exact_counts[budget] += int(result.exact)
                warnings.extend(
                    warning for warning in result.warnings if warning not in warnings
                )

        domain_tiers = []
        for budget in budgets:
            observations = rows_by_budget[budget]
            score_values = [row["score"] for row in observations]
            gain_values = [
                (score / baseline_score - 1) * 100
                for score in score_values
            ]
            hits = sum(value >= target_gain_pct for value in gain_values)
            gain_estimate = _estimate(gain_values)
            gain_rate = _estimate([
                value * 100 / budget for value in gain_values
            ])
            member_summaries = []
            for index, member in enumerate(members):
                member_summaries.append(_member_summary(
                    member,
                    role_weights[index],
                    target_sets[index],
                    baseline_rows[index],
                    [row["members"][index] for row in observations],
                    baseline_score,
                    reference_damage[index],
                ))
            search_exact_fraction = exact_counts[budget] / sample_count
            iterations_values = [row["iterations"] for row in observations]
            tier = {
                "resin": budget,
                "claims": budget // RESIN_PER_CLAIM,
                "samples": sample_count,
                "expected_account_score": _estimate(score_values),
                "expected_gain_pct": gain_estimate,
                "gain_per_100_resin_pct": gain_rate,
                "outcome_gain_pct_p10_p90": [
                    _quantile(gain_values, 0.1),
                    _quantile(gain_values, 0.9),
                ],
                "target_gain_pct": float(target_gain_pct),
                "probability_meet_target": hits / sample_count,
                "probability_meet_target_ci95": _wilson_interval(hits, sample_count),
                "shared_inventory_exact_fraction": search_exact_fraction,
                "members": member_summaries,
                "search": {
                    "exact_fraction": search_exact_fraction,
                    "mean_iterations": fsum(iterations_values) / sample_count,
                    "node_limit_per_member": ACCOUNT_SEARCH_NODES,
                },
            }
            domain_tiers.append(tier)
            rankings_by_budget[budget].append({
                "domain_id": int(domain["id"]),
                "domain_name": domain["name"],
                "gain_per_100_resin_pct": gain_rate["mean"],
            })
        domain_results.append({
            "domain_id": int(domain["id"]),
            "domain_name": domain["name"],
            "drop_sets": [
                {"id": set_id, "name": SETS[set_id]["name"]}
                for set_id in domain["set_ids"]
            ],
            "tiers": domain_tiers,
        })

    rankings = []
    for budget in budgets:
        ranked = sorted(
            rankings_by_budget[budget],
            key=lambda row: row["gain_per_100_resin_pct"],
            reverse=True,
        )
        for position, row in enumerate(ranked, 1):
            row["rank"] = position
        rankings.append({"resin": budget, "domains": ranked})
    ranking_budget = budgets[-1]
    best_ranked = rankings[-1]["domains"]
    recommended = (
        best_ranked[0]["domain_id"]
        if best_ranked and best_ranked[0]["gain_per_100_resin_pct"] > 0
        else None
    )
    return {
        "starting_inventory": starting_inventory,
        "ranking_budget": ranking_budget,
        "recommended_domain_id": recommended,
        "role_weights": {
            member.model.character: role_weights[index]
            for index, member in enumerate(members)
        },
        "baseline": {
            "account_score": baseline_score,
            "normalization": (
                "Σ 角色权重 ×（所选模型目标值 / 同角色、武器、天赋及公式配置的无圣遗物目标值）；"
                "角色权重归一化后总和为 1。"
            ),
            "members": [
                {
                    "character": member.model.character,
                    "name": member.label,
                    "role_weight": role_weights[index],
                    "reference_damage": reference_damage[index],
                    "objective": baseline_rows[index]["objective"],
                    "panel": baseline_rows[index]["panel"],
                    "formulas": baseline_rows[index]["formulas"],
                    "target_four_piece_set": (
                        {"id": target_sets[index], "name": SETS[target_sets[index]]["name"]}
                        if target_sets[index] is not None else None
                    ),
                    "target_set_complete": baseline_rows[index]["target_set_complete"],
                }
                for index, member in enumerate(members)
            ],
        },
        "rankings": rankings,
        "domains": domain_results,
        "warnings": warnings,
        "method": {
            "kind": "monte_carlo_shared_inventory_expected_optimum",
            "samples": sample_count,
            "seed": seed,
            "resin_per_claim": RESIN_PER_CLAIM,
            "five_star_drops_per_claim": 1,
            "future_artifact_level": 20,
            "target_gain_definition": (
                "账号级相对目标值提升达到阈值；账号目标按所选角色权重汇总，"
                "并以固定无圣遗物模型目标值归一化。"
            ),
            "mean_interval": "approximate 95% normal confidence interval for the sample mean",
            "outcome_interval": "empirical 10th–90th percentile interval",
            "probability_interval": "95% Wilson score interval for the target-hit rate",
            "probability_source": "core.simulation and data/data.json artifact probability tables",
            "maximum_searches": MAX_ACCOUNT_SEARCHES,
            "searches": search_count,
            "baseline_searches": 1,
            "total_searches": search_count + 1,
            "limitations": [
                "每 20 树脂按固定获得 1 件五星圣遗物估算，未计入可能的额外掉落。",
                "未来掉落随机强化至 +20；强化经验与摩拉不计入树脂预算。",
                "当前库存中的未满级圣遗物按期望满级属性投影。",
                "从零开始时允许残缺配装；目标四件套不作硬约束，成套概率按每位角色分别报告。",
                "置信区间只反映概率抽样误差，不包含共享库存争用启发式搜索的误差。",
            ],
        },
    }
