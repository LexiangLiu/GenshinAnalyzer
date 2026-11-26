"""Five-star artifact simulation with a local, versioned game-data snapshot."""

import importlib as _importlib
import sys as _sys

from damage.character_models import (
    catalog_available, catalog_metadata, catalog_models, catalog_spec, get_catalog_model,
)
from damage import calculate_damage, damage_engine_info, list_damage_formulas
from damage.damage_models import DamageModel, available_models, farming_substats, get_damage_model
from core.equipment import loadout, mastery_bonus
from planning.farming import analyze_farming
from data.game_data import catalog, characters
from inventory import Inventory
from optimization.loadout_optimizer import Loadout, LoadoutPiece, OptimizeResult, evaluate_loadout, optimize_loadout
from core.models import Artifact
from optimization.party_optimizer import PartyLoadout, PartyMember, PartyOptimizeResult, optimize_party
from planning.party_plan import (
    auto_main_stats, compact_main_stats, farming_value_for_member, plan_party,
    rank_domains_for_loadouts,
)
from planning.farming_loop import FarmRound, FarmingLoopResult, simulate_farming_loop
from optimization.weapon_optimizer import (
    WeaponChoice, WeaponMember, WeaponOption, WeaponOptimizeResult,
    optimize_weapons_and_artifacts,
)
from planning.plan import plan_single_character
from data.references import game_data_info, get_character_profile, get_game_record, get_game_stats, list_game_records
from core.resin import estimate_first_upgrade
from core.scoring import score, score_weights
from core.simulation import Simulator


_COMPAT_MODULES = {
    "build_objective": "damage.build_objective",
    "character_models": "damage.character_models",
    "damage": "damage",
    "damage_models": "damage.damage_models",
    "equipment": "core.equipment",
    "farming": "planning.farming",
    "farming_loop": "planning.farming_loop",
    "game_data": "data.game_data",
    "inventory": "inventory",
    "legacy": "inventory.legacy",
    "loadout_optimizer": "optimization.loadout_optimizer",
    "models": "core.models",
    "party_optimizer": "optimization.party_optimizer",
    "party_plan": "planning.party_plan",
    "plan": "planning.plan",
    "references": "data.references",
    "resin": "core.resin",
    "roll_probability": "core.roll_probability",
    "scoring": "core.scoring",
    "simulation": "core.simulation",
    "weapon_optimizer": "optimization.weapon_optimizer",
}

for _compat_name, _module_path in _COMPAT_MODULES.items():
    _module = _importlib.import_module(_module_path)
    _sys.modules[f"{__name__}.{_compat_name}"] = _module
    globals()[_compat_name] = _module

del _compat_name, _module_path, _module, _importlib, _sys

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
