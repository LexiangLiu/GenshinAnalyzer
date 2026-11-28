from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class BuildGoal:
    weapon_id: str | None = None
    weapon_target_level: int | None = None
    weapon_target_refinement: int | None = None
    four_piece_set: int | None = None
    main_stats: Mapping[str, str | Sequence[str]] = field(default_factory=dict)
    substat_weights: Mapping[str, float] = field(default_factory=dict)
    formula_objectives: Sequence[Mapping[str, object]] = ()

    def to_dict(self) -> dict:
        return {
            "weapon_id": self.weapon_id,
            "weapon_target_level": self.weapon_target_level,
            "weapon_target_refinement": self.weapon_target_refinement,
            "four_piece_set": self.four_piece_set,
            "main_stats": dict(self.main_stats),
            "substat_weights": dict(self.substat_weights),
            "formula_objectives": [dict(item) for item in self.formula_objectives],
        }


@dataclass(frozen=True, slots=True)
class CharacterGoal:
    character_id: str
    target_level: int | None = None
    target_constellation: int | None = None
    target_talents: Mapping[str, int] | None = None
    build: BuildGoal = field(default_factory=BuildGoal)
    short_term_priority: float = 1.0
    long_term_priority: float = 1.0

    def __post_init__(self) -> None:
        if not self.character_id:
            raise ValueError("character_id 不能为空")
        for priority in (self.short_term_priority, self.long_term_priority):
            if type(priority) not in (int, float) or priority < 0:
                raise ValueError("养成优先级必须是有限非负数")

    def to_dict(self) -> dict:
        return {
            "character_id": self.character_id,
            "target_level": self.target_level,
            "target_constellation": self.target_constellation,
            "target_talents": (
                dict(self.target_talents) if self.target_talents is not None else None
            ),
            "build": self.build.to_dict(),
            "short_term_priority": float(self.short_term_priority),
            "long_term_priority": float(self.long_term_priority),
        }


@dataclass(frozen=True, slots=True)
class AccountGoal:
    name: str
    characters: Sequence[CharacterGoal]
    horizon: str = "long"
    global_weights: Mapping[str, float] = field(default_factory=dict)
    resource_caps: Mapping[str, object] = field(default_factory=dict)
    activity_rules: Sequence[Mapping[str, object]] = ()
    status: str = "draft"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("目标名称不能为空")
        if self.horizon not in ("short", "long", "mixed"):
            raise ValueError("horizon 必须是 short、long 或 mixed")
        if self.status not in ("draft", "active", "archived"):
            raise ValueError("目标状态无效")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "horizon": self.horizon,
            "characters": [character.to_dict() for character in self.characters],
            "global_weights": dict(self.global_weights),
            "resource_caps": dict(self.resource_caps),
            "activity_rules": [dict(item) for item in self.activity_rules],
            "status": self.status,
            "schema_version": 1,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AccountGoal":
        if int(value.get("schema_version", 1)) != 1:
            raise ValueError("不支持的养成目标版本")
        characters = []
        for item in value.get("characters", ()):
            build_value = item.get("build", {})
            build = BuildGoal(
                weapon_id=build_value.get("weapon_id"),
                weapon_target_level=build_value.get("weapon_target_level"),
                weapon_target_refinement=build_value.get("weapon_target_refinement"),
                four_piece_set=build_value.get("four_piece_set"),
                main_stats=dict(build_value.get("main_stats", {})),
                substat_weights=dict(build_value.get("substat_weights", {})),
                formula_objectives=tuple(build_value.get("formula_objectives", ())),
            )
            characters.append(CharacterGoal(
                character_id=item["character_id"],
                target_level=item.get("target_level"),
                target_constellation=item.get("target_constellation"),
                target_talents=item.get("target_talents"),
                build=build,
                short_term_priority=item.get("short_term_priority", 1.0),
                long_term_priority=item.get("long_term_priority", 1.0),
            ))
        return cls(
            name=value["name"],
            horizon=value.get("horizon", "long"),
            characters=characters,
            global_weights=dict(value.get("global_weights", {})),
            resource_caps=dict(value.get("resource_caps", {})),
            activity_rules=tuple(value.get("activity_rules", ())),
            status=value.get("status", "draft"),
        )
