from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class CharacterState:
    identity: str
    level: int
    ascension: int
    constellation: int
    talents: Mapping[str, int]
    weapon_id: str | None = None
    weapon_level: int | None = None
    weapon_refinement: int | None = None
    equipped_artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.identity:
            raise ValueError("identity 不能为空")
        if type(self.level) is not int or not 1 <= self.level <= 100:
            raise ValueError("角色等级必须介于 1 和 100")
        if type(self.ascension) is not int or not 0 <= self.ascension <= 7:
            raise ValueError("角色突破阶段必须介于 0 和 7")
        if type(self.constellation) is not int or not 0 <= self.constellation <= 6:
            raise ValueError("命座等级必须介于 0 和 6")
        if not isinstance(self.talents, Mapping):
            raise ValueError("天赋必须是映射")
        for key in ("auto", "skill", "burst"):
            value = self.talents.get(key)
            if type(value) is not int or not 1 <= value <= 15:
                raise ValueError(f"天赋 {key} 必须介于 1 和 15")

    def to_dict(self) -> dict:
        value = asdict(self)
        value["talents"] = dict(self.talents)
        value["equipped_artifact_ids"] = list(self.equipped_artifact_ids)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CharacterState":
        return cls(
            identity=str(value["identity"]),
            level=int(value["level"]),
            ascension=int(value["ascension"]),
            constellation=int(value["constellation"]),
            talents=dict(value["talents"]),
            weapon_id=value.get("weapon_id"),
            weapon_level=value.get("weapon_level"),
            weapon_refinement=value.get("weapon_refinement"),
            equipped_artifact_ids=tuple(value.get("equipped_artifact_ids", ())),
        )


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    source: Mapping[str, object]
    characters: Sequence[CharacterState]
    inventory: Mapping[str, object] = field(default_factory=dict)
    equipment: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("不支持的账号快照版本")
        if not isinstance(self.source, Mapping):
            raise ValueError("source 必须是映射")
        if self.source.get("kind") not in ("simulation", "manual", "legacy_import"):
            raise ValueError("source.kind 必须是 simulation、manual 或 legacy_import")
        if not isinstance(self.characters, Sequence):
            raise ValueError("characters 必须是列表")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "source": dict(self.source),
            "characters": [character.to_dict() for character in self.characters],
            "inventory": dict(self.inventory),
            "equipment": dict(self.equipment),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AccountSnapshot":
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            source=dict(value["source"]),
            characters=[CharacterState.from_dict(item) for item in value.get("characters", ())],
            inventory=dict(value.get("inventory", {})),
            equipment=dict(value.get("equipment", {})),
            metadata=dict(value.get("metadata", {})),
        )
