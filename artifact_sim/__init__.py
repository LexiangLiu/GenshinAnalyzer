"""Five-star artifact simulation with a local, versioned game-data snapshot."""

from .character_models import (
    catalog_available, catalog_metadata, catalog_models, catalog_spec, get_catalog_model,
)
from .damage import calculate_damage, damage_engine_info, list_damage_formulas
from .damage_models import DamageModel, available_models, farming_substats, get_damage_model
from .equipment import loadout, mastery_bonus
from .farming import analyze_farming
from .game_data import catalog, characters
from .inventory import Inventory
from .loadout_optimizer import Loadout, LoadoutPiece, OptimizeResult, evaluate_loadout, optimize_loadout
from .models import Artifact
from .party_optimizer import PartyLoadout, PartyMember, PartyOptimizeResult, optimize_party
from .party_plan import (
    auto_main_stats, compact_main_stats, farming_value_for_member, plan_party,
    rank_domains_for_loadouts,
)
from .farming_loop import FarmRound, FarmingLoopResult, simulate_farming_loop
from .weapon_optimizer import (
    WeaponChoice, WeaponMember, WeaponOption, WeaponOptimizeResult,
    optimize_weapons_and_artifacts,
)
from .plan import plan_single_character
from .references import game_data_info, get_character_profile, get_game_record, get_game_stats, list_game_records
from .resin import estimate_first_upgrade
from .scoring import score, score_weights
from .simulation import Simulator

__all__ = [
    "Artifact", "Inventory", "Simulator", "catalog", "characters", "loadout", "mastery_bonus", "score", "score_weights",
    "game_data_info", "get_character_profile", "get_game_record", "get_game_stats", "list_game_records",
    "calculate_damage", "damage_engine_info", "list_damage_formulas",
    "analyze_farming", "DamageModel", "get_damage_model", "available_models", "farming_substats",
    "catalog_available", "catalog_metadata", "catalog_models", "catalog_spec", "get_catalog_model",
    "Loadout", "LoadoutPiece", "OptimizeResult", "evaluate_loadout", "optimize_loadout",
    "PartyLoadout", "PartyMember", "PartyOptimizeResult", "optimize_party",
    "plan_party", "auto_main_stats", "compact_main_stats", "farming_value_for_member",
    "rank_domains_for_loadouts",
    "WeaponChoice", "WeaponMember", "WeaponOption", "WeaponOptimizeResult",
    "optimize_weapons_and_artifacts",
    "FarmRound", "FarmingLoopResult", "simulate_farming_loop",
    "plan_single_character", "estimate_first_upgrade",
]
