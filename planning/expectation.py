"""Monte Carlo estimates for best-loadout outcomes after artifact farming."""

from __future__ import annotations

from math import fsum, isfinite, sqrt
from statistics import stdev
from typing import Sequence

from core.models import Artifact
from core.simulation import Simulator
from damage.build_objective import character_weapon_panel
from data.game_data import DOMAINS, SETS, lookup
from optimization.loadout_optimizer import LoadoutPiece, optimize_loadout


DEFAULT_RESIN_BUDGETS = (0, 200, 600, 1200)
DEFAULT_SAMPLE_COUNT = 16
MAX_SAMPLE_COUNT = 64
MAX_RESIN_BUDGET = 4000
MAX_BUDGET_TIERS = 8
MAX_OPTIMIZATIONS = 96
SEARCH_NODES_PER_SAMPLE = 20_000
RESIN_PER_CLAIM = 20
_SLOTS = ("FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET")


def _estimate(values: Sequence[float]) -> dict:
    if not values:
        return {"mean": None, "standard_error": None, "ci95": None}
    average = fsum(values) / len(values)
    if len(values) < 2:
        return {
            "mean": average,
            "standard_error": 0.0,
            "ci95": [average, average],
        }
    standard_error = stdev(values) / sqrt(len(values))
    margin = 1.96 * standard_error
    return {
        "mean": average,
        "standard_error": standard_error,
        "ci95": [average - margin, average + margin],
    }


def _panel_stats(loadout) -> dict[str, float]:
    stats = loadout.stats
    panel = {
        "HP": float(
            stats.get("BASE_HP", 0)
            * (1 + stats.get("HP_PERCENT", 0) / 100)
            + stats.get("HP", 0)
        ),
        "ATK": float(
            stats.get("BASE_ATK", 0)
            * (1 + stats.get("ATK_PERCENT", 0) / 100)
            + stats.get("ATK", 0)
        ),
        "DEF": float(
            stats.get("BASE_DEF", 0)
            * (1 + stats.get("DEF_PERCENT", 0) / 100)
            + stats.get("DEF", 0)
        ),
    }
    for stat, value in stats.items():
        if stat.startswith("BASE_") or stat in ("HP", "ATK", "DEF"):
            continue
        panel[stat] = float(value)
    return panel


def _models(model) -> tuple:
    composite = getattr(model, "models", None)
    return tuple(composite) if composite is not None else (model,)


def _measure(loadout, model) -> dict:
    formula_values = {
        formula.key: float(formula.evaluate(loadout.stats, loadout.set_flags))
        for formula in _models(model)
    }
    return {
        "panel": _panel_stats(loadout),
        "objective": float(loadout.damage),
        "formulas": formula_values,
    }


def _series(values: Sequence[dict], key: str) -> dict:
    if not values:
        return {}
    names = sorted({name for row in values for name in row[key]})
    return {
        name: _estimate([row[key].get(name, 0.0) for row in values])
        for name in names
    }


def _delta_series(values: Sequence[dict], baseline: dict, key: str) -> dict:
    if not values:
        return {}
    names = sorted(
        {name for row in values for name in row[key]}
        | set(baseline[key])
    )
    return {
        name: _estimate([
            row[key].get(name, 0.0) - baseline[key].get(name, 0.0)
            for row in values
        ])
        for name in names
    }


def estimate_loadout_expectations(
    pieces: Sequence[LoadoutPiece | Artifact],
    *,
    model,
    character: dict,
    weapon: dict | None,
    bonus_stats: dict,
    domain: str | int,
    resin_budgets: Sequence[int] = DEFAULT_RESIN_BUDGETS,
    starting_inventory: str = "inventory",
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    seed: int = 0,
    required_four_piece_set: int | None = None,
) -> dict:
    """Estimate the optimized result after adding random +20 five-star drops.

    Every 20 resin is modeled as one five-star drop, matching the existing
    planner's conservative claim assumption. Future drops are sampled from the
    repository's discrete artifact-generation tables and enhanced to +20.
    """
    domain_info = lookup(DOMAINS, domain)
    if starting_inventory not in ("inventory", "zero"):
        raise ValueError("starting_inventory 必须是 inventory 或 zero")
    if (
        not isinstance(resin_budgets, (list, tuple))
        or not resin_budgets
        or len(resin_budgets) > MAX_BUDGET_TIERS
    ):
        raise ValueError(f"resin_budgets 需包含 1–{MAX_BUDGET_TIERS} 个预算")
    budgets = []
    for budget in resin_budgets:
        if type(budget) is not int or budget < 0 or budget > MAX_RESIN_BUDGET or budget % RESIN_PER_CLAIM:
            raise ValueError(
                f"每档树脂预算须为 0–{MAX_RESIN_BUDGET} 间的 20 倍数"
            )
        budgets.append(budget)
    budgets = sorted(set(budgets))
    if len(budgets) != len(resin_budgets):
        raise ValueError("树脂预算不能重复")
    if type(sample_count) is not int or not 8 <= sample_count <= MAX_SAMPLE_COUNT:
        raise ValueError(f"sample_count 必须介于 8 和 {MAX_SAMPLE_COUNT} 之间")
    if type(seed) is not int:
        raise ValueError("seed 必须是整数")
    nonzero_tiers = sum(budget > 0 for budget in budgets)
    if nonzero_tiers * sample_count > MAX_OPTIMIZATIONS:
        raise ValueError(
            f"抽样工作量过大；预算档位与样本数的乘积不能超过 {MAX_OPTIMIZATIONS}"
        )

    if required_four_piece_set is not None:
        required_four_piece_set = int(lookup(SETS, required_four_piece_set)["id"])
    base_stats, _ = character_weapon_panel(character, weapon, bonus_stats)
    starting_pieces = list(pieces) if starting_inventory == "inventory" else []
    starting_set_count = sum(
        piece.artifact.set_id == required_four_piece_set
        for piece in starting_pieces
    ) if required_four_piece_set is not None else 0
    baseline_constraint = (
        required_four_piece_set
        if required_four_piece_set is None
        or starting_inventory == "inventory"
        else None
    )
    baseline_result = optimize_loadout(
        starting_pieces,
        model,
        base_stats,
        required_four_piece_set=baseline_constraint,
        max_nodes=SEARCH_NODES_PER_SAMPLE,
    )
    baseline = _measure(baseline_result.loadout, model)
    observations: dict[int, list[dict]] = {budget: [] for budget in budgets}
    exact_counts = {budget: 0 for budget in budgets}
    node_counts = {budget: [] for budget in budgets}

    for sample in range(sample_count):
        simulator = Simulator(seed + sample * 104729)
        future: list[LoadoutPiece] = []
        current_claim = 0
        for budget in budgets:
            target_claim = budget // RESIN_PER_CLAIM
            if target_claim == 0:
                if required_four_piece_set is None or (
                    starting_inventory == "inventory" and starting_set_count >= 4
                ):
                    observations[budget].append(baseline)
                    exact_counts[budget] += int(baseline_result.exact)
                    node_counts[budget].append(baseline_result.nodes)
                continue
            while current_claim < target_claim:
                artifact = simulator.enhance(simulator.drop(domain_info["id"]), 20)
                future.append(LoadoutPiece(artifact))
                current_claim += 1
            available_pieces = [*starting_pieces, *future]
            if required_four_piece_set is not None and sum(
                piece.artifact.set_id == required_four_piece_set
                for piece in available_pieces
            ) < 4:
                continue
            result = optimize_loadout(
                available_pieces,
                model,
                base_stats,
                required_four_piece_set=required_four_piece_set,
                max_nodes=SEARCH_NODES_PER_SAMPLE,
            )
            observations[budget].append(_measure(result.loadout, model))
            exact_counts[budget] += int(result.exact)
            node_counts[budget].append(result.nodes)

    tiers = []
    for budget in budgets:
        rows = observations[budget]
        objective_values = [row["objective"] for row in rows]
        objective_deltas = [value - baseline["objective"] for value in objective_values]
        formula_values = _series(rows, "formulas")
        tiers.append({
            "resin": budget,
            "claims": budget // RESIN_PER_CLAIM,
            "samples": sample_count,
            "feasible_samples": len(rows),
            "feasible_fraction": len(rows) / sample_count,
            "objective": _estimate(objective_values),
            "objective_delta": _estimate(objective_deltas),
            "panel": _series(rows, "panel"),
            "panel_delta": _delta_series(rows, baseline, "panel"),
            "formulas": {
                key: {
                    **value,
                    "delta": _estimate([
                        row["formulas"].get(key, 0.0) - baseline["formulas"].get(key, 0.0)
                        for row in rows
                    ]),
                }
                for key, value in formula_values.items()
            },
            "search": {
                "exact_fraction": exact_counts[budget] / len(rows) if rows else None,
                "mean_nodes": fsum(node_counts[budget]) / len(rows) if rows else None,
                "node_limit_per_sample": SEARCH_NODES_PER_SAMPLE,
            },
        })

    return {
        "domain": {
            "id": domain_info["id"],
            "name": domain_info["name"],
            "set_ids": list(domain_info["set_ids"]),
        },
        "starting_inventory": starting_inventory,
        "required_four_piece_set": (
            {
                "id": required_four_piece_set,
                "name": SETS[required_four_piece_set]["name"],
            }
            if required_four_piece_set is not None else None
        ),
        "model_label": model.label,
        "formula_labels": {
            formula.key: formula.label for formula in _models(model)
        },
        "baseline": {
            **baseline,
            "search": {
                "exact": baseline_result.exact,
                "nodes": baseline_result.nodes,
                "node_limit": SEARCH_NODES_PER_SAMPLE,
                "required_four_piece_set": baseline_constraint,
            },
        },
        "tiers": tiers,
        "method": {
            "kind": "monte_carlo_expected_optimum",
            "samples": sample_count,
            "seed": seed,
            "resin_per_claim": RESIN_PER_CLAIM,
            "five_star_drops_per_claim": 1,
            "future_artifact_level": 20,
            "confidence_interval": "approximate 95% normal interval for the sample mean",
            "probability_source": "core.simulation and data/data.json artifact probability tables",
            "limitations": [
                "树脂按每 20 点固定获得 1 件五星圣遗物估算；未计入可能的额外掉落。",
                "未来掉落会按随机词条强化至 +20；强化经验与摩拉不计入树脂预算。",
                "当前库存的未满级圣遗物应由调用方按期望满级属性投影后传入。",
                "置信区间只反映蒙特卡洛抽样误差，不包含节点上限导致的配装搜索误差。",
            ],
        },
    }
