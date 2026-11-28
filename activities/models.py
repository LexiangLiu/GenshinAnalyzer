from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ActivityRule:
    rule_id: str
    name: str
    source_type: str
    source_ref: str
    valid_from: str
    valid_until: str
    confidence: float
    verified: bool = False
    benefits: Mapping[str, object] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rule_id or not self.name:
            raise ValueError("活动规则需要 rule_id 和 name")
        if self.source_type not in ("user_input", "offline_config"):
            raise ValueError("source_type 必须是 user_input 或 offline_config")
        if not self.source_ref:
            raise ValueError("活动规则必须带可追溯 source_ref")
        if type(self.confidence) not in (int, float) or not 0 <= self.confidence <= 1:
            raise ValueError("活动置信度必须介于 0 和 1")
        for value in (self.valid_from, self.valid_until):
            try:
                datetime.fromisoformat(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("valid_from 和 valid_until 必须是 ISO 日期时间") from exc

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "confidence": float(self.confidence),
            "verified": bool(self.verified),
            "benefits": dict(self.benefits),
            "limitations": list(self.limitations),
        }
