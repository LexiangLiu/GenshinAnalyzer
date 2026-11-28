from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence

from .models import ActivityRule


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def evaluate_activity_rules(
    rules: Sequence[ActivityRule | Mapping[str, object]],
    *,
    now: str | datetime | None = None,
) -> dict:
    current = datetime.now().astimezone() if now is None else (
        now if isinstance(now, datetime) else datetime.fromisoformat(now)
    )
    accepted = []
    degraded = []
    for raw in rules:
        if isinstance(raw, Mapping):
            required = (
                "rule_id", "name", "source_type", "source_ref",
                "valid_from", "valid_until", "confidence",
            )
            if any(field not in raw for field in required):
                degraded.append({
                    "rule": {
                        "rule_id": str(raw.get("rule_id") or raw.get("kind") or "unstructured"),
                        "name": str(raw.get("name") or "未命名活动配置"),
                        "source_type": "user_input",
                        "source_ref": "unstructured_activity_rule",
                        "valid_from": "",
                        "valid_until": "",
                        "confidence": 0.0,
                        "verified": False,
                        "benefits": dict(raw.get("benefits", {})),
                        "limitations": list(raw.get("limitations", ())),
                    },
                    "status": "unverified",
                    "reason": "活动配置缺少来源、有效期或置信度，未计入收益",
                })
                continue
            try:
                rule = ActivityRule(
                    rule_id=str(raw["rule_id"]),
                    name=str(raw["name"]),
                    source_type=str(raw["source_type"]),
                    source_ref=str(raw["source_ref"]),
                    valid_from=str(raw["valid_from"]),
                    valid_until=str(raw["valid_until"]),
                    confidence=float(raw["confidence"]),
                    verified=bool(raw.get("verified", False)),
                    benefits=dict(raw.get("benefits", {})),
                    limitations=tuple(raw.get("limitations", ())),
                )
            except (KeyError, TypeError, ValueError) as exc:
                degraded.append({
                    "rule": {
                        "rule_id": str(raw.get("rule_id") or "invalid"),
                        "name": str(raw.get("name") or "无效活动配置"),
                        "source_type": str(raw.get("source_type") or "user_input"),
                        "source_ref": str(raw.get("source_ref") or "invalid_activity_rule"),
                        "valid_from": str(raw.get("valid_from") or ""),
                        "valid_until": str(raw.get("valid_until") or ""),
                        "confidence": 0.0,
                        "verified": False,
                        "benefits": {},
                        "limitations": [str(exc)],
                    },
                    "status": "unverified",
                    "reason": "活动配置无效，未计入收益",
                })
                continue
        else:
            rule = raw
        if not rule.verified:
            degraded.append({
                "rule": rule.to_dict(),
                "status": "unverified",
                "reason": "未验证活动规则不计入收益",
            })
            continue
        if rule.confidence < 0.7:
            degraded.append({
                "rule": rule.to_dict(),
                "status": "low_confidence",
                "reason": "置信度低于可靠收益门槛 0.70",
            })
            continue
        start = _parse(rule.valid_from)
        end = _parse(rule.valid_until)
        if current.tzinfo is not None:
            if start.tzinfo is None:
                start = start.replace(tzinfo=current.tzinfo)
            if end.tzinfo is None:
                end = end.replace(tzinfo=current.tzinfo)
        if current < start:
            degraded.append({
                "rule": rule.to_dict(),
                "status": "upcoming",
                "reason": "活动尚未生效，不计入当前收益",
            })
        elif current > end:
            degraded.append({
                "rule": rule.to_dict(),
                "status": "expired",
                "reason": "活动已过期，不计入收益",
            })
        else:
            accepted.append({
                "rule": rule.to_dict(),
                "status": "active",
                "reason": "已验证且在有效期内",
            })
    return {"accepted": accepted, "degraded": degraded}
