from __future__ import annotations

from dataclasses import replace
from random import Random
from typing import Mapping, Sequence

from ..data.game_data import DATA, DOMAINS, SETS, lookup
from .models import MILESTONES, Artifact, Enhancement, Roll, validate_integer


_FIVE_STAR_SETS = tuple(item["id"] for item in SETS.values() if 5 in item["rarities"])


class Simulator:
    def __init__(self, seed: int | None = None) -> None:
        self._rng = Random(seed)

    def _draw(self, weights: Mapping):
        return self._rng.choices(tuple(weights), weights=tuple(weights.values()))[0]

    def _tier(self, weights: Sequence[float]) -> int:
        return self._rng.choices((1, 2, 3, 4), weights=weights)[0]

    def drop(self, domain: str | int, *, four_line_probability: float | None = None) -> Artifact:
        domain_info = lookup(DOMAINS, domain)
        return self.create(self._rng.choice(domain_info["set_ids"]), four_line_probability=four_line_probability)

    def create(
        self,
        set_name: str | int | None = None,
        *,
        slot: str | None = None,
        main_stat: str | None = None,
        initial_count: int | None = None,
        substats: Sequence[tuple[str, int | None]] = (),
        four_line_probability: float | None = None,
    ) -> Artifact:
        """Create a five-star piece. The fourth custom roll is pending on a three-liner."""
        set_info = SETS[self._rng.choice(_FIVE_STARSETS)] if set_name is None else lookup(SETS, set_name)
        if 5 not in set_info["rarities"]:
            raise ValueError(f"{set_info['name']} 没有五星圣遗物")
        if slot is None:
            if main_stat is not None:
                raise ValueError("指定主词条时也需要指定部位")
            slot = self._draw(DATA["slot_weights"])
        if slot not in DATA["main_stat_weights"]:
            raise ValueError(f"未知的部位：{slot}")
        main_weights = DATA["main_stat_weights"][slot]
        if main_stat is None:
            main_stat = self._draw(main_weights)
        if main_stat not in main_weights:
            raise ValueError(f"{slot} 不支持主词条 {main_stat}")
        if initial_count is not None and four_line_probability is not None:
            raise ValueError("初始词条数与四词条概率只能指定其中一个")
        if four_line_probability is not None and not 0 <= four_line_probability <= 1:
            raise ValueError("四词条概率必须在 0 到 1 之间")
        if initial_count is None:
            weights = DATA["initial_count_weights"]
            if four_line_probability is not None:
                weights = {"3": 1 - four_line_probability, "4": four_line_probability}
            initial_count = int(self._draw(weights))
        validate_integer(initial_count, 3, 4, "初始激活词条数")

        available = {stat: weight for stat, weight in DATA["substat_weights"].items() if stat != main_stat}
        if len(substats) > 4:
            raise ValueError("最多指定四个副词条")
        rolls = []
        for stat, tier in substats:
            if stat not in available:
                raise ValueError(f"副词条无效、重复或与主词条相同：{stat}")
            rolls.append(Roll(stat, self._tier(DATA["tier_weights"]) if tier is None else tier))
            del available[stat]
        while len(rolls) < 4:
            stat = self._draw(available)
            rolls.append(Roll(stat, self._tier(DATA["tier_weights"])))
            del available[stat]
        return Artifact(set_info["id"], slot, main_stat, initial_count, tuple(rolls))

    def enhance(
        self, artifact: Artifact, to_level: int, *,
        overrides: Mapping[int, tuple[str | None, int | None]] | None = None,
    ) -> Artifact:
        """Enhance to a level using stored unlock data and optional forced later rolls."""
        validate_integer(to_level, artifact.level, 20, "目标等级")
        milestones = [
            level for level in MILESTONES
            if artifact.level < level <= to_level and (level != 4 or artifact.initial_count == 4)
        ]
        forced = overrides or {}
        stats = [roll.stat for roll in artifact.initial_rolls]
        for level, (stat, tier) in forced.items():
            if level not in milestones or (stat is not None and stat not in stats):
                raise ValueError(f"+{level} 无法指定强化 {stat}，三词条的 +4 只激活预览词条")
            if tier is not None:
                validate_integer(tier, 1, 4, "强化档位")
        events = list(artifact.enhancements)
        for level in milestones:
            stat, tier = forced.get(level, (None, None))
            slot = stats.index(stat) if stat is not None else self._rng.choices(
                range(4), weights=DATA["upgrade_slot_weights"],
            )[0]
            tier = self._tier(DATA["upgrade_tier_weights"]) if tier is None else tier
            events.append(Enhancement(level, slot, tier))
        return replace(artifact, level=to_level, enhancements=tuple(events))
