"""Projected +20 roll distributions. No random draws are used here."""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from functools import lru_cache
from itertools import permutations
from math import lcm

from core.models import Artifact, MILESTONES
from data.game_data import DATA, SUB_VALUES


def probabilities(weights):
    weights = {key: Fraction(str(value)) for key, value in weights.items()}
    total = sum(weights.values())
    return {key: weight / total for key, weight in weights.items() if weight}


_INITIAL_TIERS = probabilities(dict(enumerate(DATA["tier_weights"])))
_UPGRADE_TIERS = probabilities(dict(enumerate(DATA["upgrade_tier_weights"])))
_TARGET_WEIGHTS = probabilities(dict(enumerate(DATA["upgrade_slot_weights"])))
_ROLL_VALUES = {stat: tuple(int(Fraction(str(value)) * 100) for value in values)
                for stat, values in SUB_VALUES.items()}
_INITIAL_MEAN = {stat: sum(values[tier] * weight for tier, weight in _INITIAL_TIERS.items()) / 100
                 for stat, values in _ROLL_VALUES.items()}
_UPGRADE_MEAN = {stat: sum(values[tier] * weight for tier, weight in _UPGRADE_TIERS.items()) / 100
                 for stat, values in _ROLL_VALUES.items()}


def _expected_roll_value(stat: str, tiers) -> Fraction:
    return sum(
        Fraction(_ROLL_VALUES[stat][tier], 100) * weight
        for tier, weight in tiers.items()
    )


def expected_full_level_substats(artifact: Artifact) -> dict[str, float]:
    """Return expected substat values at +20 without sampling or using hidden previews."""
    current = artifact.substats()
    expected = {stat: Fraction(str(value)) for stat, value in current.items()}
    active = artifact.initial_rolls[:4 if artifact.level >= 4 or artifact.initial_count == 4 else 3]
    future_levels = tuple(level for level in MILESTONES if artifact.level < level)

    if artifact.initial_count == 3 and artifact.level < 4:
        weights = {
            stat: Fraction(str(weight))
            for stat, weight in DATA["substat_weights"].items()
            if stat != artifact.main_stat and stat not in current
        }
        total = sum(weights.values())
        fourth_line = {
            stat: weight / total
            for stat, weight in weights.items()
        }
        for stat, probability in fourth_line.items():
            expected[stat] = expected.get(stat, Fraction(0)) + probability * _expected_roll_value(
                stat, _INITIAL_TIERS,
            )

        future_upgrades = sum(level > 4 for level in future_levels)
        for position, roll in enumerate(active):
            expected[roll.stat] = expected.get(roll.stat, Fraction(0)) + (
                future_upgrades * _TARGET_WEIGHTS[position]
                * _expected_roll_value(roll.stat, _UPGRADE_TIERS)
            )
        for stat, probability in fourth_line.items():
            expected[stat] += (
                future_upgrades * probability * _TARGET_WEIGHTS[3]
                * _expected_roll_value(stat, _UPGRADE_TIERS)
            )
    else:
        for level in future_levels:
            if level == 4 and artifact.initial_count == 3:
                continue
            for position, roll in enumerate(active):
                expected[roll.stat] += (
                    _TARGET_WEIGHTS[position]
                    * _expected_roll_value(roll.stat, _UPGRADE_TIERS)
                )

    return {stat: float(value) for stat, value in expected.items()}


@lru_cache(maxsize=512)
def roll_groups(main_stat: str, tracked: tuple[str, ...], extra: tuple[str, ...]):
    """Group ordered four-line selections with the same projected upgrade law."""
    weights = {stat: weight for stat, weight in DATA["substat_weights"].items() if stat != main_stat}
    total_weight = sum(weights.values())
    groups = {}
    for order in permutations(weights, 4):
        remaining = total_weight
        mass = Fraction(1)
        for stat in order:
            mass *= Fraction(weights[stat], remaining)
            remaining -= weights[stat]
        targets = {stat: _TARGET_WEIGHTS.get(position, Fraction(0)) for position, stat in enumerate(order)}
        group = tuple((stat, targets[stat]) for stat in tracked if stat in targets)
        if group not in groups:
            groups[group] = [Fraction(0), defaultdict(Fraction), defaultdict(Fraction)]
        totals = groups[group]
        totals[0] += mass
        for stat in extra:
            if stat in targets:
                totals[1][stat] += mass
                totals[2][stat] += mass * targets[stat]
    result = []
    for group, (mass, inclusions, targets) in groups.items():
        miss_probability = 1 - sum(weight for _, weight in group)
        first_means = tuple(float(_INITIAL_MEAN[stat] * inclusions[stat] / mass) for stat in extra)
        missed_means = tuple(
            float(_UPGRADE_MEAN[stat] * targets[stat] / (mass * miss_probability)) if miss_probability else 0.0
            for stat in extra
        )
        result.append((group, float(mass), first_means, missed_means))
    return tuple(result)


def _integer_events(events):
    denominator = lcm(*(mass.denominator for _, _, mass in events))
    return denominator, tuple((position, value, int(mass * denominator)) for position, value, mass in events)


@lru_cache(maxsize=128)
def roll_outcomes(group, need_misses: bool):
    """Return integer outcome weights and the weighted number of off-axis hits."""
    states = {(0,) * len(group): [1, 0]}
    denominator = 1
    for position, (stat, _) in enumerate(group):
        scale, events = _integer_events(
            [(position, _ROLL_VALUES[stat][tier], mass) for tier, mass in _INITIAL_TIERS.items()]
        )
        states = {values[:axis] + (value,) + values[axis + 1:]: [mass * weight, 0]
                  for values, (mass, _) in states.items() for axis, value, weight in events}
        denominator *= scale

    events = [(position, _ROLL_VALUES[stat][tier], target * mass)
              for position, (stat, target) in enumerate(group) if target
              for tier, mass in _UPGRADE_TIERS.items()]
    miss = 1 - sum(target for _, target in group)
    if miss:
        events.append((-1, 0, miss))
    scale, events = _integer_events(events)
    initial_counts = probabilities(DATA["initial_count_weights"])
    combined = defaultdict(lambda: [0, 0])
    count_scale = lcm(*(weight.denominator for weight in initial_counts.values()))
    for level in range(1, 6):
        updated = defaultdict(lambda: [0, 0])
        for values, (mass, misses) in states.items():
            for axis, value, weight in events:
                key = values if axis < 0 else values[:axis] + (values[axis] + value,) + values[axis + 1:]
                result = updated[key]
                result[0] += mass * weight
                if need_misses:
                    result[1] += (misses + (mass if axis < 0 else 0)) * weight
        states = updated
        if level >= 4:
            factor = int(initial_counts.get(str(level - 1), 0) * count_scale) * scale ** (5 - level)
            for key, (mass, misses) in states.items():
                result = combined[key]
                result[0] += mass * factor
                result[1] += misses * factor
    return denominator * scale ** 5 * count_scale, dict(combined)
