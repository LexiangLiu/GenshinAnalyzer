from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ResourceLine:
    resource_id: str | int
    name: str
    count: int
    source: str
    status: str = "known"
    remaining: int | None = None

    def __post_init__(self) -> None:
        if self.status not in ("known", "unknown"):
            raise ValueError("资源状态必须是 known 或 unknown")
        if type(self.count) is not int or self.count < 0:
            raise ValueError("资源数量必须是非负整数")

    def to_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "name": self.name,
            "count": self.count,
            "source": self.source,
            "status": self.status,
            "remaining": self.remaining,
        }


@dataclass(frozen=True, slots=True)
class ResourceRequirement:
    lines: Sequence[ResourceLine] = ()
    unknown: Sequence[Mapping[str, object]] = ()
    actions: Sequence[Mapping[str, object]] = ()

    def to_dict(self) -> dict:
        return {
            "lines": [line.to_dict() for line in self.lines],
            "unknown": [dict(item) for item in self.unknown],
            "actions": [dict(item) for item in self.actions],
        }
