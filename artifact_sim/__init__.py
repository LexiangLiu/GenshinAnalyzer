"""Five-star artifact simulation with a local, versioned game-data snapshot."""

import importlib as _importlib
import sys as _sys

from genshin_simulator.damage.character_models import (
    catalog_available, catalog_metadata, catalog_models, catalog_spec, get_catalog_model,
)
from genshin_simulator.damage import calculate_damage, damage_engine_info, list_damage_formulas
from genshin_simulator.damage.damage_models import DamageModel, available_models, farming_substats, get_damage_model
from genshin_simulator.core.equipment import loadout, mastery_bonus
from genshin_simulator.planning.farming import analyze_farming
from genshin_simulator.data.game_data import catalog, characters
from genshin_simulator.inventory import Inventory
from genshin_simulator.optimization.loadout_optimizer import Loadout, LoadoutPiece, OptimizeResult, evaluate_loadout, optimize_loadout
from genshin_simulator.core.models import Artifact
from genshin_simulator.optimization.party_optimizer import PartyLoadout, PartyMember, PartyOptimizeResult, optimize_party
from genshin_simulator.planning.party_plan import (
    auto_main_stats, compact_main_stats, farming_value_for_member, plan_party,
    rank_domains_for_loadouts,
)
from genshin_simulator.planning.farming_loop import FarmRound, FarmingLoopResult, simulate_farming_loop
from genshin_simulator.optimization.weapon_optimizer import (
    WeaponChoice, WeaponMember, WeaponOption, WeaponOptimizeResult,
    optimize_weapons_and_artifacts,
)
from genshin_simulator.planning.plan import plan_single_character
from genshin_simulator.data.references import game_data_info, get_character_profile, get_game_record, get_game_stats, list_game_records
from genshin_simulator.core.resin import estimate_first_upgrade
from genshin_simulator.core.scoring import score, score_weights
from genshin_simulator.core.simulation import Simulator


_COMPAT_MODULES = {
    "build_objective": "genshin_simulator.damage.build_objective",
    "character_models": "genshin_simulator.damage.character_models",
    "damage": "genshin_simulator.damage",
    "damage_models": "genshin_simulator.damage.damage_models",
    "equipment": "genshin_simulator.core.equipment",
    "farming": "genshin_simulator.planning.farming",
    "farming_loop": "genshin_simulator.planning.farming_loop",
    "game_data": "genshin_simulator.data.game_data",
    "inventory": "genshin_simulator.inventory",
    "legacy": "genshin_simulator.inventory.legacy",
    "loadout_optimizer": "genshin_simulator.optimization.loadout_optimizer",
    "models": "genshin_simulator.core.models",
    "party_optimizer": "genshin_simulator.optimization.party_optimizer",
    "party_plan": "genshin_simulator.planning.party_plan",
    "plan": "genshin_simulator.planning.plan",
    "references": "genshin_simulator.data.references",
    "resin": "genshin_simulator.core.resin",
    "roll_probability": "genshin_simulator.core.roll_probability",
    "scoring": "genshin_simulator.core.scoring",
    "simulation": "genshin_simulator.core.simulation",
    "weapon_optimizer": "genshin_simulator.optimization.weapon_optimizer",
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
