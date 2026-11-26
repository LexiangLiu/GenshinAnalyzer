from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Mapping, Sequence

from data.game_data import DATA, MAIN_VALUES, SETS, SUB_VALUES
from .scoring import score, score_weights


MILESTONES = (4, 8, 12, 16, 20)


def validate_integer(value: int, low: int, high: int, description: str) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{description}必须是 {low} 到 {high} 的整数")


@dataclass(frozen=True, slots=True)
class Roll:
    stat: str
    tier: int

    def __post_init__(self) -> None:
        if self.stat not in SUB_VALUES:
            raise ValueError(f"未知的副词条：{self.stat}")
        validate_integer(self.tier, 1, 4, "副词条档位")

    @property
    def value(self) -> float:
        return SUB_VALUES[self.stat][self.tier - 1]


@dataclass(frozen=True, slots=True)
class Enhancement:
    level: int
    slot: int
    tier: int

    def __post_init__(self) -> None:
        validate_integer(self.level, 4, 20, "强化记录等级")
        validate_integer(self.slot, 0, 3, "强化位置")
        validate_integer(self.tier, 1, 4, "强化档位")


@dataclass(frozen=True, slots=True)
class Artifact:
    set_id: int
    slot: str
    main_stat: str
    initial_count: int
    initial_rolls: tuple[Roll, ...]
    level: int = 0
    enhancements: tuple[Enhancement, ...] = ()

    def __post_init__(self) -> None:
        if self.set_id not in SETS or 5 not in SETS[self.set_id]["rarities"]:
            raise ValueError(f"未知的五星套装：{self.set_id}")
        if self.main_stat not in DATA["main_stat_weights"].get(self.slot, {}):
            raise ValueError(f"{self.slot} 不支持主词条 {self.main_stat}")
        validate_integer(self.initial_count, 3, 4, "初始激活词条数")
        validate_integer(self.level, 0, 20, "等级")
        stats = {roll.stat for roll in self.initial_rolls}
        if len(self.initial_rolls) != 4 or len(stats) != 4 or self.main_stat in stats:
            raise ValueError("必须保存四个互异且与主词条不同的副词条")
        reached = tuple(level for level in MILESTONES if level <= self.level)
        if self.initial_count == 3:
            reached = reached[1:]
        if tuple(event.level for event in self.enhancements) != reached:
            raise ValueError("强化记录与等级或初始词条数不匹配")

    @property
    def main_value(self) -> float:
        return MAIN_VALUES[self.main_stat][self.level]

    @property
    def preview(self) -> Roll | None:
        return self.initial_rolls[3] if self.initial_count == 3 and self.level < 4 else None

    def substats(self) -> dict[str, float]:
        active_count = self.initial_count if self.level < 4 else 4
        stats = {
            roll.stat: Decimal(str(roll.value)) for roll in self.initial_rolls[:active_count]
        }
        for event in self.enhancements:
            roll = Roll(self.initial_rolls[event.slot].stat, event.tier)
            stats[roll.stat] += Decimal(str(roll.value))
        return {stat: float(value) for stat, value in stats.items()}

    def score(self, weights: Mapping[str, float] | None = None) -> float:
        """Score active substats. Defaults to the attack and crit presets."""
        return score(self.substats(), weights)

    def reset(self) -> Artifact:
        return replace(self, level=0, enhancements=())

    def to_state(self) -> dict:
        return {"schema_version": 1, **asdict(self)}

    @classmethod
    def from_state(cls, state: dict) -> Artifact:
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("不支持的圣遗物存档版本")
        return cls(
            set_id=state["set_id"], slot=state["slot"], main_stat=state["main_stat"],
            initial_count=state["initial_count"], level=state["level"],
            initial_rolls=tuple(Roll(**item) for item in state["initial_rolls"]),
            enhancements=tuple(Enhancement(**item) for item in state["enhancements"]),
        )

    def describe(self, language: str = "zh", rules: str | Sequence[str] = "default") -> dict:
        preview = self.preview
        suffix = "_en" if language == "en" else ""
        history = []
        if self.initial_count == 3 and self.level >= 4:
            roll = self.initial_rolls[3]
            history.append({"level": 4, "kind": "activate", **asdict(roll), "value": roll.value})
        for event in self.enhancements:
            roll = Roll(self.initial_rolls[event.slot].stat, event.tier)
            history.append({"level": event.level, "kind": "increase", **asdict(roll), "value": roll.value})
        return {
            "set_name": SETS[self.set_id]["name" + suffix],
            "piece_name": SETS[self.set_id]["pieces" + suffix][self.slot],
            "rarity": 5,
            "state": self.to_state(),
            "main_value": self.main_value,
            "active_substats": self.substats(),
            "pending_substat": None if preview is None else {
                **asdict(preview), "value": preview.value, "activates_at": 4,
            },
            "crit_value": self.score(score_weights("crit")),
            "score": self.score(score_weights(rules)),
            "history": history,
        }
