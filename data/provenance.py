from __future__ import annotations

from typing import Mapping

def provenance_snapshot(activity_rules: list[Mapping[str, object]] | None = None) -> dict:
    from damage.character_models import catalog_metadata
    from .references import game_data_info
    game = game_data_info()
    catalog = catalog_metadata()
    activities = []
    for rule in activity_rules or []:
        if not isinstance(rule, Mapping):
            continue
        activities.append({
            "rule_id": rule.get("rule_id") or rule.get("kind"),
            "name": rule.get("name"),
            "source_type": rule.get("source_type", "unstructured"),
            "source_ref": rule.get("source_ref"),
            "valid_from": rule.get("valid_from"),
            "valid_until": rule.get("valid_until"),
            "confidence": rule.get("confidence"),
            "verified": bool(rule.get("verified", False)),
            "status": (
                "provenance_complete"
                if rule.get("source_ref")
                and rule.get("valid_from")
                and rule.get("valid_until")
                and type(rule.get("confidence")) in (int, float)
                else "provenance_incomplete"
            ),
        })
    return {
        "schema_version": 1,
        "game_data": {
            "schema_version": game.get("schema_version"),
            "game_version": game.get("game_version"),
            "sources": game.get("sources", {}),
            "character_category_sha256": (
                game.get("categories", {})
                .get("characters", {})
                .get("sha256", {})
            ),
            "character_stats_sha256": (
                game.get("stats", {})
                .get("characters", {})
                .get("sha256")
            ),
        },
        "formula_catalog": {
            "schema_version": catalog.get("schema_version"),
            "generated_at": catalog.get("generated_at"),
            "upstream": catalog.get("upstream"),
            "model_count": catalog.get("count"),
        },
        "activities": activities,
        "unknown": [
            {
                "field": "real_account_sync",
                "status": "not_implemented",
            },
            {
                "field": "live_activity_fetch",
                "status": "not_implemented",
            },
        ],
    }
