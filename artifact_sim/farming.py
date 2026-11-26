"""Exact discrete domain farming evaluation against an equipped build."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from functools import lru_cache
from math import exp, expm1, fsum, isfinite, log, log1p
from typing import Sequence

from .build_objective import BuildObjective, as_decimal, character_weapon_panel
from .equipment import set_effects
from .game_data import DATA, DOMAINS, MAIN_VALUES, SETS, SUB_VALUES, lookup
from .models import Artifact
from .roll_probability import probabilities, roll_groups, roll_outcomes


_SLOTS = tuple(DATA["slot_weights"])
_SLOT_MASSES = probabilities(DATA["slot_weights"])
_MAIN_MASSES = {slot: probabilities(weights) for slot, weights in DATA["main_stat_weights"].items()}


class _Budget:
    def __init__(self, *, drops, resin, five_star_per_claim):
        if (drops is None) == (resin is None):
            raise ValueError("请指定 drops 或 resin 之一")
        amount = drops if resin is None else resin
        if type(amount) is not int or amount < 0:
            raise ValueError("预算必须是非负整数")
        self.resin = resin
        if resin is None:
            if five_star_per_claim is not None:
                raise ValueError("固定件数预算无需填写 five_star_per_claim")
            self.repeats = drops
            self.claim = ((1, 1.0),)
            self.details = {"drops": drops}
            return
        if resin % 20:
            raise ValueError("resin 必须是 20 的倍数")
        if not isinstance(five_star_per_claim, dict) or not five_star_per_claim:
            raise ValueError("树脂预算需提供每 20 树脂五星件数分布 five_star_per_claim")
        law = {}
        for key, value in five_star_per_claim.items():
            if type(key) is int:
                count = key
            elif isinstance(key, str) and key.isascii() and key.isdigit():
                count = int(key)
            else:
                raise ValueError("单次五星件数必须是非负整数")
            mass = as_decimal(value)
            if count < 0 or count in law or not 0 <= mass <= 1:
                raise ValueError("单次五星件数必须互异且非负，概率须在 0 到 1 之间")
            law[count] = mass
        if sum(law.values()) != 1:
            raise ValueError("five_star_per_claim 概率之和必须为 1")
        self.repeats = resin // 20
        self.claim = tuple((count, float(mass)) for count, mass in sorted(law.items()) if mass)
        self.details = {"resin": resin, "claims": self.repeats, "five_star_per_claim": dict(self.claim)}

    def log_cdf(self, tail: float) -> float:
        """Log of the chance all pieces fit below a threshold, given the one-piece tail."""
        if not self.repeats or tail <= 0:
            return 0.0
        log_drop_cdf = log1p(-tail) if tail < 1 else -float("inf")
        claim_tail = fsum(mass * -expm1(count * log_drop_cdf) for count, mass in self.claim if count)
        if claim_tail >= 0.5:
            # Use log-sum-exp so a claim with many drops cannot underflow here.
            terms = [log(mass) + (count * log_drop_cdf if count else 0.0) for count, mass in self.claim]
            peak = max(terms)
            return self.repeats * (peak + log(fsum(exp(term - peak) for term in terms))) if peak != -float("inf") else peak
        return self.repeats * log1p(-claim_tail)


def _stats(pieces, base):
    stats = base.copy()
    for piece in pieces:
        stats[piece.main_stat] += as_decimal(piece.main_value)
        for stat, value in piece.substats().items():
            stats[stat] += as_decimal(value)
    return stats


def _effects(counts):
    stats, bonuses = set_effects(counts)
    active = frozenset(f"SET_{bonus['set_id']}_{bonus['pieces']}" for bonus in bonuses)
    return stats, active


def _merge_score(histogram, score, mass, moments):
    entry = histogram.get(score)
    if entry is None:
        histogram[score] = [mass, *moments]
    else:
        entry[0] += mass
        for i, moment in enumerate(moments, 1):
            entry[i] += moment


@lru_cache(maxsize=8192)
def _roll_value(value):
    return Decimal(value) / 100


def _replacement_histogram(objective, baseline, minimum_score, stats, active, main, reports, tracked, evaluate=None):
    evaluate = objective.evaluate if evaluate is None else evaluate
    histogram = {}
    extra = tuple(stat for stat in reports if stat not in tracked)
    for group, group_mass, first, missed in roll_groups(main, tracked, extra):
        denominator, outcomes = roll_outcomes(group, bool(extra))
        names = tuple(stat for stat, _ in group)
        positions = {stat: i for i, stat in enumerate(names)}
        extra_positions = {stat: i for i, stat in enumerate(extra)}
        indices = [(positions.get(stat), extra_positions.get(stat)) for stat in reports]
        scale = group_mass / denominator
        for values, (weight, miss_weight) in outcomes.items():
            if not weight:
                continue
            increases = {stat: _roll_value(value) for stat, value in zip(names, values)}
            candidate_score = evaluate(stats, active, increases)
            if candidate_score <= baseline or candidate_score < minimum_score:
                continue
            mass = scale * weight
            moments = (
                mass * values[pos] / 100 if pos is not None else
                scale * (weight * first[off_axis] + miss_weight * missed[off_axis]) if off_axis is not None else 0.0
                for pos, off_axis in indices
            )
            _merge_score(histogram, candidate_score, mass, moments)
    return histogram


def _replacement_totals(objective, baseline, minimum_score, stats, active, main, tracked, evaluate=None):
    """Aggregate single-drop mass and gain without keeping a score histogram."""
    evaluate = objective.evaluate if evaluate is None else evaluate
    baseline_value = float(baseline)
    total_mass = 0.0
    total_gain = 0.0
    for group, group_mass, _, _ in roll_groups(main, tracked, ()):
        denominator, outcomes = roll_outcomes(group, False)
        names = tuple(stat for stat, _ in group)
        scale = group_mass / denominator
        for values, (weight, _) in outcomes.items():
            if not weight:
                continue
            increases = {stat: _roll_value(value) for stat, value in zip(names, values)}
            candidate_score = evaluate(stats, active, increases)
            if candidate_score <= baseline or candidate_score < minimum_score:
                continue
            mass = scale * weight
            total_mass += mass
            total_gain += mass * (float(candidate_score) - baseline_value)
    return total_mass, total_gain


def _summary_totals(mass, gain, baseline, budget, threshold):
    baseline_value = float(baseline)
    gain_percent = gain / baseline_value * 100 if baseline_value > 0 else None
    report = {
        "single_drop_upgrade_probability": min(mass, 1.0),
        "upgrade_probability": min(mass, 1.0),
        "expected_objective": baseline_value + gain,
        "expected_gain": gain,
        "expected_gain_pct": gain_percent,
        "expected_final_substats": {},
    }
    if budget.resin is not None:
        rate = gain_percent * 100 / budget.resin if budget.resin and gain_percent is not None else None
        report["expected_gain_per_resin"] = gain / budget.resin if budget.resin else None
        report["expected_gain_pct_per_100_resin"] = rate
        if threshold is not None:
            report["meets_minimum_gain_rate"] = rate >= threshold if rate is not None else None
    return report


def _summarize(histogram, baseline, original_substats, reports, budget, threshold):
    baseline_value = float(baseline)
    if budget.repeats == 1 and budget.claim == ((1, 1.0),):
        # A single five-star can produce each score bucket with its own mass;
        # no geometric budget math is needed in this common planning path.
        tail = 0.0
        gain = 0.0
        expected_changes = [0.0] * len(reports)
        for score, (mass, *moments) in histogram.items():
            tail += mass
            gain += mass * (float(score) - baseline_value)
            for i, moment in enumerate(moments):
                expected_changes[i] += moment
        gain_percent = gain / baseline_value * 100 if baseline_value > 0 else None
        report = {
            "single_drop_upgrade_probability": min(tail, 1.0),
            "upgrade_probability": min(tail, 1.0),
            "expected_objective": baseline_value + gain,
            "expected_gain": gain,
            "expected_gain_pct": gain_percent,
            "expected_final_substats": {stat: fsum((current, change))
                                        for stat, current, change in zip(reports, original_substats, expected_changes)},
        }
        if budget.resin is not None:
            rate = gain_percent * 100 / budget.resin if budget.resin and gain_percent is not None else None
            report["expected_gain_per_resin"] = gain / budget.resin if budget.resin else None
            report["expected_gain_pct_per_100_resin"] = rate
            if threshold is not None:
                report["meets_minimum_gain_rate"] = rate >= threshold if rate is not None else None
        return report
    gains = []
    expected_changes = [0.0] * len(reports)
    tail = 0.0
    upper_log = 0.0
    for score in sorted(histogram, reverse=True):
        mass, *moments = histogram[score]
        tail += mass
        lower_log = budget.log_cdf(tail)
        upper_cdf = exp(upper_log)
        probability = upper_cdf * -expm1(lower_log - upper_log) if upper_cdf else 0.0
        upper_log = lower_log
        gains.append(probability * (float(score) - baseline_value))
        for i, moment in enumerate(moments):
            expected_changes[i] += probability * (moment / mass)
    gain = fsum(gains)
    gain_percent = gain / baseline_value * 100 if baseline_value > 0 else None
    report = {
        "single_drop_upgrade_probability": min(tail, 1.0),
        "upgrade_probability": -expm1(upper_log) if tail and budget.repeats else 0.0,
        "expected_objective": baseline_value + gain,
        "expected_gain": gain,
        "expected_gain_pct": gain_percent,
        "expected_final_substats": {stat: fsum((current, change))
                                    for stat, current, change in zip(reports, original_substats, expected_changes)},
    }
    if budget.resin is not None:
        rate = gain_percent * 100 / budget.resin if budget.resin and gain_percent is not None else None
        report["expected_gain_per_resin"] = gain / budget.resin if budget.resin else None
        report["expected_gain_pct_per_100_resin"] = rate
        if threshold is not None:
            report["meets_minimum_gain_rate"] = rate >= threshold if rate is not None else None
    return report


def analyze_farming(
    equipped: Sequence[Artifact], *, objective: str, domains: Sequence[str | int] | None = None,
    character: dict | None = None, weapon: dict | None = None, bonus_stats: dict | None = None,
    drops: int | None = None, resin: int | None = None, five_star_per_claim: dict | None = None,
    main_stats: dict | None = None, preserve_set_bonuses: bool = True, target_four_piece_set: str | int | None = None,
    report_substats: Sequence[str] | None = None, min_gain_pct_per_100_resin: float | None = None,
    min_upgrade_pct: float = 0.0, score_function=None, summary_only: bool = False,
    tracked_substats: Sequence[str] | None = None,
) -> dict:
    """Evaluate the best strict single replacement after a finite farming budget.

    ``min_upgrade_pct`` filters replacements that improve the objective by less
    than the given percentage. The default of zero means any strict improvement.
    ``summary_only`` skips score histograms and only keeps per-slot/domain
    probability and expected gain; it is intended for single-drop planning.
    ``tracked_substats`` optionally narrows the substats carried through the
    exact roll enumeration; plans use it to keep multi-character runs bounded.
    """
    budget = _Budget(drops=drops, resin=resin, five_star_per_claim=five_star_per_claim)
    if type(min_upgrade_pct) not in (int, float) or not isfinite(min_upgrade_pct) or min_upgrade_pct < 0:
        raise ValueError("min_upgrade_pct 必须是有限的非负数")
    if score_function is not None and not callable(score_function):
        raise ValueError("score_function 必须可调用")
    if not isinstance(summary_only, bool):
        raise ValueError("summary_only 必须是布尔值")
    formula = BuildObjective(objective)
    if tracked_substats is not None:
        if not isinstance(tracked_substats, (list, tuple)):
            raise ValueError("tracked_substats 必须是副词条名称列表")
        tracked = tuple(dict.fromkeys(tracked_substats))
        if any(stat not in SUB_VALUES for stat in tracked):
            raise ValueError("tracked_substats 只能包含副词条名称")
    else:
        tracked = formula.substats
    if report_substats is not None and not isinstance(report_substats, (list, tuple)):
        raise ValueError("report_substats 必须是副词条名称列表")
    reports = tracked if report_substats is None else tuple(dict.fromkeys(report_substats))
    if any(stat not in SUB_VALUES for stat in reports):
        raise ValueError("report_substats 只能包含副词条名称")
    if summary_only and reports:
        raise ValueError("summary_only 不支持报告副词条期望")
    if summary_only and not (budget.repeats == 1 and budget.claim == ((1, 1.0),)):
        raise ValueError("summary_only 只支持单件掉落预算")
    if type(preserve_set_bonuses) is not bool:
        raise ValueError("preserve_set_bonuses 必须是布尔值")
    if min_gain_pct_per_100_resin is not None:
        threshold = float(as_decimal(min_gain_pct_per_100_resin))
        if resin is None or threshold < 0:
            raise ValueError("收益门槛需要树脂预算且不能为负")
    else:
        threshold = None
    target = lookup(SETS, target_four_piece_set) if target_four_piece_set is not None else None
    if target is not None and (5 not in target["rarities"] or "4" not in target["effects"]):
        raise ValueError("target_four_piece_set 必须是有四件套效果的五星套装")
    if domains is None and target is not None:
        domains = [domain_id for domain_id, info in DOMAINS.items() if target["id"] in info["set_ids"]]
        if not domains:
            raise ValueError(f"当前掉落表没有产出{target['name']}的秘境，请指定 domains")
    if not isinstance(domains, (list, tuple)) or not domains:
        raise ValueError("请指定目标四件套或至少一个秘境")
    selected_domains = {row["id"]: row for row in (lookup(DOMAINS, domain) for domain in domains)}
    candidates = {slot: tuple(masses) for slot, masses in _MAIN_MASSES.items()}
    if main_stats is not None and not isinstance(main_stats, dict):
        raise ValueError("main_stats 必须是部位到候选主词条列表的对象")
    for slot, mains in (main_stats or {}).items():
        if slot not in candidates or not isinstance(mains, (list, tuple)) or any(
            main not in _MAIN_MASSES[slot] for main in mains
        ):
            raise ValueError(f"{slot} 的候选主词条无效")
        candidates[slot] = tuple(dict.fromkeys(mains))
    current = {artifact.slot: artifact for artifact in equipped}
    if len(current) != len(equipped):
        raise ValueError("同一配装不能装备两个同部位圣遗物")
    innate, references = character_weapon_panel(character, weapon, bonus_stats)
    counts = Counter(piece.set_id for piece in equipped)
    if target is not None and counts[target["id"]] < 3:
        raise ValueError("当前配装至少需要三件目标套装，才可能经一次替换凑齐四件套")
    current_bonuses, active_sets = _effects(counts)
    target_flag = f"SET_{target['id']}_4" if target is not None else None
    starting_panel = _stats(equipped, innate)
    for stat, value in current_bonuses.items():
        starting_panel[stat] += value
    baseline = formula.evaluate(starting_panel, active_sets)
    minimum_score = baseline * (1 + as_decimal(min_upgrade_pct) / 100)
    summaries = {slot: None for slot in _SLOTS}
    piece_substats = {slot: piece.substats() for slot, piece in current.items()}
    for slot, piece in current.items():
        summaries[slot] = {"set_id": piece.set_id, "set_name": SETS[piece.set_id]["name"],
                           "level": piece.level, "main_stat": piece.main_stat, "substats": piece_substats[slot]}
    starting_substats = tuple(fsum(values.get(stat, 0.0) for values in piece_substats.values()) for stat in reports)
    relevant = formula.names | set(tracked)

    def assemble(domain_slots, overall_reports):
        results = []
        for domain_id, domain in selected_domains.items():
            slots = domain_slots[domain_id]
            results.append({
                "domain_id": domain_id, "domain_name": domain["name"],
                "drop_sets": [{"id": set_id, "name": SETS[set_id]["name"]} for set_id in domain["set_ids"]],
                "overall": overall_reports[domain_id],
                "slots_by_upgrade_probability": sorted(
                    slots, key=lambda slot: slots[slot]["upgrade_probability"], reverse=True,
                ),
                "slots_by_expected_gain": sorted(
                    slots, key=lambda slot: slots[slot]["expected_gain"], reverse=True,
                ),
                "slots": slots,
            })
        results.sort(key=lambda entry: entry["overall"]["expected_gain"], reverse=True)
        recommended = next(
            (entry["domain_id"] for entry in results
             if entry["overall"]["expected_gain"] > 0
             and (threshold is None or entry["overall"].get("meets_minimum_gain_rate"))),
            None,
        )
        return {
            "objective": objective, "replacement_limit": 1, "candidate_level": 20,
            "budget": budget.details, "preserve_set_bonuses": preserve_set_bonuses if target is None else None,
            "target_four_piece_set": {"id": target["id"], "name": target["name"]} if target is not None else None,
            "min_gain_pct_per_100_resin": threshold, "min_upgrade_pct": float(min_upgrade_pct),
            "tracked_substats": list(tracked),
            "candidate_main_stats": candidates,
            "baseline": {"objective": float(baseline), "equipped": summaries, **references,
                         "active_set_flags": sorted(active_sets), "bonus_stats": bonus_stats or {},
                         "substats": dict(zip(reports, starting_substats))},
            "recommended_domain_id": recommended, "domains_by_expected_gain": results,
        }

    if summary_only:
        by_domain_totals = {domain_id: [0.0, 0.0] for domain_id in selected_domains}
        domain_slot_totals = {domain_id: {} for domain_id in selected_domains}
        for slot in _SLOTS:
            after_removal = counts.copy()
            if slot in current:
                after_removal[current[slot].set_id] -= 1
            fixed = _stats([piece for piece in equipped if piece.slot != slot], innate)
            conditional = {}
            for domain_id, domain in selected_domains.items():
                total_mass = 0.0
                total_gain = 0.0
                weight = float(_SLOT_MASSES[slot]) / len(domain["set_ids"])
                for set_id in domain["set_ids"]:
                    static, active = _effects(after_removal + Counter({set_id: 1}))
                    if target_flag is not None:
                        if target_flag not in active:
                            continue
                    elif preserve_set_bonuses and not active_sets <= active:
                        continue
                    flags = frozenset(active & formula.names)
                    for main in candidates[slot]:
                        stats = fixed.copy()
                        stats[main] += as_decimal(MAIN_VALUES[main][20])
                        for stat, value in static.items():
                            stats[stat] += value
                        excluded = main if main in SUB_VALUES else None
                        key = excluded, flags, tuple(sorted((stat, stats[stat]) for stat in relevant if stat in stats))
                        if key not in conditional:
                            conditional[key] = _replacement_totals(
                                formula, baseline, minimum_score, stats, flags, main, tracked,
                                evaluate=score_function,
                            )
                        mass, gain = conditional[key]
                        scale = weight * float(_MAIN_MASSES[slot][main])
                        total_mass += mass * scale
                        total_gain += gain * scale
                domain_slot_totals[domain_id][slot] = (total_mass, total_gain)
                by_domain_totals[domain_id][0] += total_mass
                by_domain_totals[domain_id][1] += total_gain
        domain_slots = {
            domain_id: {
                slot: _summary_totals(mass, gain, baseline, budget, threshold)
                for slot, (mass, gain) in totals.items()
            }
            for domain_id, totals in domain_slot_totals.items()
        }
        overall_reports = {
            domain_id: _summary_totals(mass, gain, baseline, budget, threshold)
            for domain_id, (mass, gain) in by_domain_totals.items()
        }
        return assemble(domain_slots, overall_reports)

    # Cache conditional score histograms only while processing one slot. A change in set ID
    # with identical relevant effects shares the same result.
    by_domain = {domain_id: {} for domain_id in selected_domains}
    domain_slots = {domain_id: {} for domain_id in selected_domains}
    for slot in _SLOTS:
        after_removal = counts.copy()
        if slot in current:
            after_removal[current[slot].set_id] -= 1
        fixed = _stats([piece for piece in equipped if piece.slot != slot], innate)
        old = tuple(piece_substats.get(slot, {}).get(stat, 0.0) for stat in reports)
        conditional = {}
        for domain_id, domain in selected_domains.items():
            histogram = {}
            weight = float(_SLOT_MASSES[slot]) / len(domain["set_ids"])
            for set_id in domain["set_ids"]:
                static, active = _effects(after_removal + Counter({set_id: 1}))
                if target_flag is not None:
                    if target_flag not in active:
                        continue
                elif preserve_set_bonuses and not active_sets <= active:
                    continue
                flags = frozenset(active & formula.names)
                for main in candidates[slot]:
                    stats = fixed.copy()
                    stats[main] += as_decimal(MAIN_VALUES[main][20])
                    for stat, value in static.items():
                        stats[stat] += value
                    excluded = main if main in SUB_VALUES else None
                    key = excluded, flags, tuple(sorted((stat, stats[stat]) for stat in relevant if stat in stats))
                    if key not in conditional:
                        conditional[key] = _replacement_histogram(
                            formula, baseline, minimum_score, stats, flags, main, reports, tracked,
                            evaluate=score_function,
                        )
                    scale = weight * float(_MAIN_MASSES[slot][main])
                    for score, (mass, *moments) in conditional[key].items():
                        _merge_score(histogram, score, mass * scale, (moment * scale for moment in moments))
            for score, (mass, *moments) in histogram.items():
                changes = [moment - mass * value for moment, value in zip(moments, old)]
                _merge_score(by_domain[domain_id], score, mass, changes)
                histogram[score][1:] = changes
            domain_slots[domain_id][slot] = _summarize(histogram, baseline, starting_substats, reports, budget, threshold)
    overall_reports = {
        domain_id: _summarize(by_domain[domain_id], baseline, starting_substats, reports, budget, threshold)
        for domain_id in selected_domains
    }
    return assemble(domain_slots, overall_reports)
