from __future__ import annotations

from typing import Mapping

from .models import AccountSnapshot


def import_account_snapshot(payload: object) -> AccountSnapshot:
    """Validate a structured local account snapshot; never call an external API."""
    if not isinstance(payload, Mapping):
        raise ValueError("账号导入内容必须是 JSON 对象")
    if (
        payload.get("export_format") is not None
        and payload.get("export_format") != "genshin-analyzer-account"
    ):
        raise ValueError("不支持的账号导出格式")
    value = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else payload
    source = value.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("账号导入必须提供 source")
    kind = source.get("kind")
    if kind not in ("manual", "legacy_import", "simulation"):
        raise ValueError("账号导入只支持 manual、legacy_import 或本地 simulation；external_api 不被接受")
    if kind == "simulation" and payload.get("export_format") != "genshin-analyzer-account":
        raise ValueError("simulation 来源只能通过 genshin-analyzer-account 导出格式导入")
    if kind == "legacy_import" and not isinstance(value.get("inventory"), Mapping):
        raise ValueError("legacy_import 需要结构化 inventory")
    snapshot = AccountSnapshot.from_dict(value)
    metadata = dict(snapshot.metadata)
    if payload.get("export_format") != "genshin-analyzer-account":
        missing = list(metadata.get("missing_fields", ()))
        if "import_source" not in missing:
            missing.append("import_source")
        metadata["missing_fields"] = sorted(set(missing))
        metadata["import_status"] = "validated_local_structured"
    return AccountSnapshot(
        source=dict(snapshot.source),
        characters=snapshot.characters,
        inventory=dict(snapshot.inventory),
        equipment=dict(snapshot.equipment),
        metadata=metadata,
        schema_version=snapshot.schema_version,
    )


def import_account_snapshot_batch(payload: object) -> dict:
    """Validate a local batch and preserve per-item failures as visible results."""
    if not isinstance(payload, Mapping):
        raise ValueError("批量账号导入内容必须是 JSON 对象")
    rows = payload.get("snapshots")
    if not isinstance(rows, list):
        raise ValueError("批量账号导入需要 snapshots 数组")
    accepted = []
    rejected = []
    seen_sources = set()
    for index, row in enumerate(rows):
        try:
            snapshot = import_account_snapshot(row)
            identities = [character.identity for character in snapshot.characters]
            duplicate_identities = sorted({
                identity
                for identity in identities
                if identities.count(identity) > 1
            })
            if duplicate_identities:
                raise ValueError(
                    "账号快照包含重复角色身份："
                    + ", ".join(duplicate_identities)
                )
            external_id = snapshot.source.get("external_id")
            if external_id:
                source_key = (snapshot.source.get("kind"), str(external_id))
                if source_key in seen_sources:
                    raise ValueError("批量导入包含重复来源标识")
                seen_sources.add(source_key)
            accepted.append({
                "index": index,
                "snapshot": snapshot.to_dict(),
            })
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append({
                "index": index,
                "error": str(exc),
            })
    return {
        "schema_version": 1,
        "external_api": "not_implemented",
        "accepted": accepted,
        "rejected": rejected,
        "counts": {
            "total": len(rows),
            "accepted": len(accepted),
            "rejected": len(rejected),
        },
    }
