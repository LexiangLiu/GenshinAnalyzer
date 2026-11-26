from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import (
    Artifact, Inventory, LoadoutPiece, PartyMember, Simulator, analyze_farming, auto_main_stats,
    catalog, characters, get_damage_model, loadout, optimize_party, plan_party, plan_single_character,
)
from .damage.character_models import catalog_available, catalog_spec
from .damage import calculate_damage, damage_engine_info, list_damage_formulas
from .data.game_data import SETS, character_profile, lookup
from .inventory.legacy import legacy_backup
from .data.references import game_data_info, get_character_profile, get_game_record, get_game_stats, list_game_records


def _read_artifact(path: str) -> Artifact:
    content = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig")
    state = json.loads(content)
    if isinstance(state, dict):
        state = state.get("state", state)
    return Artifact.from_state(state)


def _roll(token: str) -> tuple[str, int | None]:
    stat, sep, tier = token.partition(":")
    return stat.strip().upper(), int(tier) if sep else None


def _override(token: str) -> tuple[int, tuple[str | None, int | None]]:
    level, sep, roll = token.partition(":")
    stat, tier = _roll(roll)
    if not sep or not stat and tier is None:
        raise ValueError("指定强化使用 LEVEL:STAT、LEVEL::TIER 或 LEVEL:STAT:TIER")
    return int(level), (stat or None, tier)


def _overrides(tokens):
    result = dict(_override(token) for token in tokens)
    if len(result) != len(tokens):
        raise ValueError("同一级只能指定一次强化")
    return result


def _generation_options(parser):
    parser.add_argument("--set", dest="set_name", help="套装中英文名称或 ID，省略时随机抽取")
    parser.add_argument("--slot", type=str.upper, choices=["FLOWER", "PLUME", "SANDS", "GOBLET", "CIRCLET"])
    parser.add_argument("--main", type=str.upper)
    initial = parser.add_mutually_exclusive_group()
    initial.add_argument("--lines", type=int, choices=[3, 4])
    initial.add_argument("--four-line-probability", type=float)
    parser.add_argument("--sub", action="append", default=[], metavar="STAT[:TIER]")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--count", type=int, default=1)


def _generate(args):
    if args.count < 1:
        raise ValueError("件数必须大于 0")
    sim = Simulator(args.seed)
    domain = getattr(args, "domain", None)
    if domain is not None:
        if any((args.set_name, args.slot, args.main, args.lines, args.sub)):
            raise ValueError("秘境掉落不能同时指定套装、部位或词条")
        for _ in range(args.count):
            yield sim.drop(domain, four_line_probability=args.four_line_probability)
    else:
        rolls = [_roll(token) for token in args.sub]
        for _ in range(args.count):
            yield sim.create(args.set_name, slot=args.slot, main_stat=args.main, initial_count=args.lines,
                             four_line_probability=args.four_line_probability, substats=rolls)


def _inventory_parser(commands):
    inventory = commands.add_parser("inventory", help="持久化库存、配装和设置")
    inventory.add_argument("--db", type=Path, default=Path("artifacts.sqlite3"))
    sub = inventory.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="生成并存入库存")
    _generation_options(add)
    add.add_argument("--domain")
    listing = sub.add_parser("list", help="筛选和排序")
    listing.add_argument("--slot", action="append", type=str.upper, default=[])
    listing.add_argument("--main", action="append", type=str.upper, default=[])
    listing.add_argument("--set", dest="sets", action="append", default=[])
    listing.add_argument("--sort", choices=["lvasc", "lvdesc", "part", "main"], default="lvdesc")
    for action, description in (
        ("get", "查看单件"), ("lock", "上锁"), ("unlock", "解锁"), ("seen", "标为已看"),
        ("delete", "删除单件"), ("enhance", "强化到下一档或指定等级"),
    ):
        command = sub.add_parser(action, help=description)
        command.add_argument("id")
        if action == "enhance":
            command.add_argument("--to", type=int)
            command.add_argument("--force", action="append", default=[], metavar="LEVEL:STAT:TIER")
            command.add_argument("--seed", type=int)
    reset = sub.add_parser("reset", help="重置单件，省略 ID 时重置未上锁的全部")
    reset.add_argument("id", nargs="?")
    for action, description in (
        ("undo", "撤销最近一次单件删除"), ("counts", "按部位、主词条和套装计数"),
        ("clear", "清空未上锁且未装备的圣遗物"), ("export", "导出库存、配装和设置"),
        ("profiles", "查看配装列表"), ("wipe", "清空库存、配装和设置"),
    ):
        sub.add_parser(action, help=description)
    for action in ("restore", "import-legacy"):
        importing = sub.add_parser(action, help="恢复本工具备份" if action == "restore" else "迁移旧版导出的 JSON")
        importing.add_argument("file", type=Path)
        importing.add_argument("--replace", action="store_true", help="替换当前全部库存和设置")
    prefs = sub.add_parser("preferences", help="查看、修改或重置设置")
    prefs.add_argument("--language", choices=["zh", "en"])
    prefs.add_argument("--score", action="append", choices=["default", "atk", "crit", "def", "hp", "er", "em"])
    prefs.add_argument("--high-score", type=float)
    prefs.add_argument("--reset", action="store_true")
    for action in ("profile", "panel", "equip", "unequip"):
        command = sub.add_parser(action)
        command.add_argument("name", help="角色名称、ID 或自定义配装名称")
        if action == "profile":
            mutation = command.add_mutually_exclusive_group()
            mutation.add_argument("--rename")
            mutation.add_argument("--delete", action="store_true")
        elif action == "equip":
            command.add_argument("ids", nargs="+")
            command.add_argument("--preview", action="store_true")
        elif action == "unequip":
            command.add_argument("--slot", action="append", type=str.upper, default=[])


def _inventory_command(args):
    inv = Inventory(args.db)
    action = args.action
    if action == "add":
        return {"ids": inv.add(_generate(args))}
    if action == "list":
        return inv.list(slots=args.slot, main_stats=args.main, sets=args.sets, sort=args.sort)
    if action == "get":
        return inv.get(args.id)
    if action in ("lock", "unlock", "seen"):
        return inv.flags(args.id, locked={"lock": True, "unlock": False}.get(action), seen=action == "seen")
    if action == "enhance":
        return inv.enhance(args.id, Simulator(args.seed), args.to, _overrides(args.force))
    if action == "reset":
        return {"reset": inv.reset(args.id)}
    if action == "delete":
        inv.delete(args.id)
        return {"deleted": args.id}
    if action == "undo":
        return {"restored": inv.undo_delete()}
    if action == "counts":
        return inv.counts()
    if action == "clear":
        return {"deleted_count": inv.clear()}
    if action == "export":
        return inv.export()
    if action in ("restore", "import-legacy"):
        backup = json.loads(args.file.read_text(encoding="utf-8-sig"))
        if action == "import-legacy":
            backup = legacy_backup(backup)
        return {"imported": inv.restore(backup, replace=args.replace)}
    if action == "wipe":
        inv.wipe()
        return {"wiped": True}
    if action == "preferences":
        changes = {key: value for key, value in (
            ("language", args.language), ("score_rules", args.score), ("high_score", args.high_score),
        ) if value is not None}
        if changes and args.reset:
            raise ValueError("重置设置不能与设置改动同时使用")
        return inv.preferences(changes or None, reset=args.reset)
    if action == "profiles":
        return inv.profiles()
    if action == "profile":
        return {"profile": inv.profile(args.name, rename=args.rename, delete=args.delete)}
    if action == "panel":
        return inv.panel(args.name)
    if action == "equip":
        return inv.equip(args.name, args.ids, preview=args.preview)
    if action == "unequip":
        return {"unequipped": inv.unequip(args.name, args.slot)}
    raise ValueError(f"未知库存操作：{action}")


def _write_json(value: object, path: Path | None) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path is None:
        sys.stdout.write(content)
        return
    handle = NamedTemporaryFile(dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content.encode("utf-8"))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _data_parser(commands):
    data = commands.add_parser("data", help="查询离线游戏资料")
    actions = data.add_subparsers(dest="action", required=True)
    actions.add_parser("info", help="查看版本、数据源与分类覆盖")
    for action, help_text in (
        ("list", "搜索或筛选类别"), ("show", "按名称或 ID 查询详情"),
        ("character", "查询角色、天赋和命座"), ("stats", "查询对应等级的基础属性"),
    ):
        query = actions.add_parser(action, help=help_text)
        query.add_argument("--language", choices=["zh", "en"], default="zh")
        if action != "character":
            query.add_argument("category")
        if action == "list":
            query.add_argument("--query", help="按名称、ID 或资料键搜索")
            query.add_argument("--version", help="筛选首次收录的游戏版本")
            query.add_argument("--where", action="append", default=[], metavar="FIELD=VALUE")
            query.add_argument("--limit", type=int)
            query.add_argument("--offset", type=int, default=0)
        else:
            query.add_argument("query", help="中英文名称、ID 或资料键")
        if action == "stats":
            query.add_argument("--level", type=int, required=True)
            query.add_argument("--ascension", default="before", help="before、after 或突破阶段编号")


def _data_command(args):
    if args.action == "info":
        return game_data_info()
    if args.action == "character":
        return get_character_profile(args.query, language=args.language)
    if args.action == "show":
        return get_game_record(args.category, args.query, language=args.language)
    if args.action == "stats":
        phase = int(args.ascension) if args.ascension.isdecimal() else args.ascension
        return get_game_stats(args.category, args.query, args.level, ascension=phase, language=args.language)
    filters = {}
    for pair in args.where:
        key, sep, raw = pair.partition("=")
        if not sep or not key or key in filters:
            raise ValueError("筛选条件须使用互异的 FIELD=VALUE")
        try:
            filters[key] = json.loads(raw)
        except json.JSONDecodeError:
            filters[key] = raw
    return list_game_records(args.category, language=args.language, search=args.query,
                             version=args.version, filters=filters, limit=args.limit, offset=args.offset)


def _damage_parser(commands):
    damage = commands.add_parser("damage", help="调用 Genshin Optimizer 伤害公式")
    damage.add_argument("--node", default="node", help="Node.js 可执行文件")
    actions = damage.add_subparsers(dest="action", required=True)
    actions.add_parser("info", help="查看公式引擎上游版本和依赖")
    formulas = actions.add_parser("formulas", help="列出角色的伤害公式和条件")
    formulas.add_argument("character", help="Genshin Optimizer 角色键，例如 Neuvillette")
    evaluate = actions.add_parser("evaluate", help="执行一份 JSON 伤害计算请求")
    evaluate.add_argument("file", help="JSON 路径，或 - 读取标准输入")


def _damage_command(args):
    if args.action == "info":
        return damage_engine_info()
    if args.action == "formulas":
        return list_damage_formulas(args.character, node=args.node)
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8-sig")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("伤害计算请求必须是 JSON 对象")
    return calculate_damage(payload, node=args.node)


def _models_parser(commands):
    models = commands.add_parser("models", help="列出和查看角色伤害模型")
    actions = models.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="列出已注册的模型")
    listing.add_argument("--element", type=str.upper)
    listing.add_argument("--weapon", type=str.upper)
    listing.add_argument("--set", dest="set_name")
    listing.add_argument("--query")
    listing.add_argument("--accuracy", choices=["high", "medium", "low", "unvalidated"])
    show = actions.add_parser("show", help="查看一个角色的模型与标定来源")
    show.add_argument("query")
    show.add_argument("--model")


def _models_command(args):
    from .damage.damage_models import available_models
    catalog_rows = catalog_available()
    for key, row in available_models().items():
        catalog_rows.setdefault(key, {**row, "accuracy": "hand-written"})
    if args.action == "list":
        target_set = lookup(SETS, args.set_name)["id"] if args.set_name else None
        rows = []
        for key, row in catalog_rows.items():
            if args.element and str(row.get("element", "")).upper() != args.element:
                continue
            if args.weapon and str(row.get("weapon_type", "")).upper() != args.weapon:
                continue
            if target_set is not None and row.get("target_four_piece_set") != target_set:
                continue
            if args.accuracy and row.get("accuracy") != args.accuracy:
                continue
            if args.query:
                token = args.query.casefold()
                if token not in " ".join(
                    str(row.get(field, "")) for field in ("key", "character", "character_zh", "label")
                ).casefold():
                    continue
            rows.append({"key": key, **row})
        return {"count": len(rows), "models": rows}
    model = get_damage_model(args.query, args.model)
    result = {"model": model.describe()}
    try:
        result["catalog"] = catalog_spec(model.key)
    except ValueError:
        result["catalog"] = None
    return result


def _party_parser(commands):
    party = commands.add_parser("party", help="多角色共享库存的最优配装与副本收益推荐")
    party.add_argument("--db", type=Path, default=Path("artifacts.sqlite3"))
    actions = party.add_subparsers(dest="action", required=True)
    for action, help_text in (
        ("optimize", "只计算联合最优配装"),
        ("plan", "联合配装并推荐继续刷取的秘境"),
    ):
        command = actions.add_parser(action, help=help_text)
        command.add_argument("file", help="JSON 请求路径，或 - 读取标准输入")


def _load_json_object(path: str, label: str) -> dict:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{label}必须是 JSON 对象")
    return value


def _party_pieces(config: dict, db: Path) -> list[LoadoutPiece]:
    raw = config.get("pieces")
    if raw is not None:
        if not isinstance(raw, list):
            raise ValueError("pieces 必须是圣遗物状态数组")
        pieces = []
        for item in raw:
            state = item.get("state", item) if isinstance(item, dict) else item
            pieces.append(LoadoutPiece(Artifact.from_state(state)))
        return pieces
    if not db.is_file():
        raise ValueError(f"库存数据库不存在：{db}")
    inventory = Inventory(db)
    return [LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
            for artifact_id, artifact in inventory.artifacts()]


def _party_members(config: dict) -> list[dict]:
    rows = config.get("members")
    if not isinstance(rows, list) or not rows:
        raise ValueError("members 必须是非空数组")
    members = []
    for row in rows:
        if not isinstance(row, dict) or "character" not in row:
            raise ValueError("每个成员都需要 character")
        weapon = row.get("weapon")
        if weapon is not None and (not isinstance(weapon, dict) or "id" not in weapon):
            raise ValueError("weapon 必须是含 id 的对象")
        normalized_weapon = None
        if weapon is not None:
            normalized_weapon = {
                "id": weapon["id"],
                "level": int(weapon.get("level", 90)),
                "ascension": int(weapon.get("ascension", 6)),
            }
        options = dict(row.get("model_options") or {})
        common = {
            "level": int(row.get("level", 90)),
            "ascension": int(row.get("ascension", 6)),
            "constellation": int(row.get("constellation", 0)),
            "talents": row.get("talents", {"auto": 10, "skill": 10, "burst": 10}),
            "crit_mode": row.get("crit_mode", "average"),
        }
        weapon_level = int(weapon.get("level", 90)) if weapon else 90
        weapon_ascension = int(weapon.get("ascension", 6)) if weapon else 6
        refinement = int(weapon.get("refinement", 1)) if weapon else 1
        common.update({
            "weapon_level": weapon_level,
            "weapon_ascension": weapon_ascension,
            "refinement": refinement,
        })
        if weapon is not None:
            common["weapon"] = weapon["id"]
        common.update(options)
        model = get_damage_model(row["character"], row.get("model"), **common)
        target = row.get("target_four_piece_set")
        target_id = None if target is None else int(lookup(SETS, target)["id"])
        members.append({
            "member": PartyMember(
                model=model,
                character={
                    "id": row["character"],
                    "level": int(row.get("level", 90)),
                    "ascension": int(row.get("ascension", 6)),
                },
                weapon=normalized_weapon,
                bonus_stats=row.get("bonus_stats"),
                weight=float(row.get("weight", 1.0)),
                target_four_piece_set=target_id,
                name=row.get("name") or row.get("profile"),
            ),
            "candidate_main_stats": row.get("candidate_main_stats"),
            "profile": row.get("profile"),
        })
    return members


def _party_current(members: list[dict], db: Path):
    if not any(row.get("profile") for row in members):
        return None
    if not db.is_file():
        raise ValueError(f"库存数据库不存在：{db}")
    inventory = Inventory(db)
    result = []
    for row in members:
        profile = row.get("profile")
        if not profile:
            result.append(None)
            continue
        result.append([
            LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
            for artifact_id, artifact in inventory.equipped_artifacts(profile)
        ])
    return result


def _party_command(args):
    config = _load_json_object(args.file, "party 请求")
    rows = _party_members(config)
    members = [row["member"] for row in rows]
    pieces = _party_pieces(config, args.db)
    common = {
        "max_nodes": int(config.get("max_nodes", 2_000_000)),
        "restarts": int(config.get("restarts", 4)),
        "seed": int(config.get("seed", 0)),
        "max_iterations": int(config.get("max_iterations", 8)),
        "normalize": bool(config.get("normalize", True)),
    }
    if args.action == "optimize":
        return optimize_party(pieces, members, **common).describe()
    current = _party_current(rows, args.db)
    return plan_party(
        pieces, members,
        domains=config.get("domains"),
        threshold_pct=float(config.get("threshold_pct", 1.0)),
        current=current,
        candidate_main_stats=[row["candidate_main_stats"] for row in rows],
        resin_per_claim=int(config.get("resin_per_claim", 20)),
        score_mode=str(config.get("score_mode", "sum")),
        **common,
    )


def _farming_command(args):
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8-sig")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("评估请求必须是 JSON 对象")
    states = payload.pop("equipped", None)
    if args.profile is not None:
        if states is not None:
            raise ValueError("equipped 与 --profile 只能使用其中一个")
        if not args.db.is_file():
            raise ValueError(f"库存数据库不存在：{args.db}")
        states = list(Inventory(args.db).panel(args.profile)["equipped"].values())
        if not states:
            raise ValueError(f"配装 {args.profile} 没有已装备的圣遗物；空配装可通过 equipped: [] 评估")
    if not isinstance(states, list) or any(not isinstance(state, dict) for state in states):
        raise ValueError("提供 equipped 圣遗物状态数组，或者使用 --profile 读取库存配装")
    return analyze_farming([Artifact.from_state(state.get("state", state)) for state in states], **payload)


def _talents(token: str) -> dict:
    parts = token.split("/")
    if len(parts) != 3:
        raise ValueError("--talents 需要 AUTO/SKILL/BURST 三段")
    try:
        values = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError("--talents 的每一段都必须是整数") from exc
    if any(not 1 <= value <= 15 for value in values):
        raise ValueError("--talents 的等级必须在 1 到 15 之间")
    return dict(zip(("auto", "skill", "burst"), values))


def _plan_parser(commands):
    plan = commands.add_parser("plan", help="单角色最优配装与刷取期望树脂")
    plan.add_argument("--character", required=True, help="角色中英文名称或 ID")
    plan.add_argument("--model", help="伤害模型键；省略时使用角色默认模型")
    plan.add_argument("--level", type=int, default=90)
    plan.add_argument("--ascension", type=int, default=6)
    plan.add_argument("--talents", default="10/10/10", help="普攻/元素战技/元素爆发")
    plan.add_argument("--constellation", type=int, default=0)
    plan.add_argument("--weapon", help="武器中英文名称或 ID；省略时不装备武器")
    plan.add_argument("--weapon-level", type=int, default=90)
    plan.add_argument("--weapon-ascension", type=int, default=6)
    plan.add_argument("--refinement", type=int, default=1)
    plan.add_argument("--a1-stacks", type=int, default=0)
    plan.add_argument("--a4-hp", type=int, default=50)
    plan.add_argument("--weapon-stacks", type=int, default=3)
    plan.add_argument("--four-piece-stacks", type=int, default=3)
    plan.add_argument("--crit-mode", choices=["average", "noncrit"], default="average")
    plan.add_argument("--four-piece-set", help="目标四件套名称或 ID；省略时使用模型默认")
    plan.add_argument("--domain", action="append", default=[], help="秘境名称或 ID；可重复")
    plan.add_argument("--threshold", type=float, default=1.0, help="有意义的相对提升百分比")
    plan.add_argument("--resin-per-claim", type=int, default=20)
    plan.add_argument("--all-main-stats", action="store_true", help="不限制候选主词条（较慢）")
    plan.add_argument("--profile", help="当前配装名称，用于和最优配装对比")
    plan.add_argument("--db", type=Path, default=Path("artifacts.sqlite3"))


def _plan_command(args):
    if not args.db.is_file():
        raise ValueError(f"库存数据库不存在：{args.db}")
    inventory = Inventory(args.db)
    pieces = [
        LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
        for artifact_id, artifact in inventory.artifacts()
    ]
    current = None
    if args.profile is not None:
        current = [
            LoadoutPiece(artifact_id=artifact_id, artifact=artifact)
            for artifact_id, artifact in inventory.equipped_artifacts(args.profile)
        ]
        if not current:
            raise ValueError(f"配装 {args.profile} 没有已装备的圣遗物")
    talents = _talents(args.talents)
    canonical = character_profile(args.character)
    weapon = args.weapon
    if weapon is None and canonical == "Neuvillette":
        weapon = "万世流涌大典"
    options = {
        "level": args.level, "ascension": args.ascension, "talents": talents,
        "constellation": args.constellation, "crit_mode": args.crit_mode,
        "weapon_level": args.weapon_level, "weapon_ascension": args.weapon_ascension,
        "refinement": args.refinement,
    }
    if weapon is not None:
        options["weapon"] = weapon
    if canonical == "Neuvillette":
        options.update({
            "a1_stacks": args.a1_stacks, "a4_hp": args.a4_hp,
            "weapon_ca_stacks": args.weapon_stacks,
            "four_piece_stacks": args.four_piece_stacks,
        })
    model = get_damage_model(args.character, args.model, **options)
    weapon_panel = None if weapon is None else {
        "id": weapon, "level": args.weapon_level, "ascension": args.weapon_ascension,
    }
    return plan_single_character(
        pieces,
        model=model,
        character={"id": args.character, "level": args.level, "ascension": args.ascension},
        weapon=weapon_panel,
        candidate_main_stats=None if args.all_main_stats else auto_main_stats(model),
        four_piece_set=args.four_piece_set,
        domains=args.domain or None,
        threshold_pct=args.threshold,
        current=current,
        resin_per_claim=args.resin_per_claim,
    )


def main() -> None:
    # Emit UTF-8 for both help and JSON when piped on Windows.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="离线五星圣遗物模拟器")
    parser.add_argument("-o", "--output", type=Path, help="将 JSON 保存到指定文件")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("catalog", help="查询版本、套装和秘境")
    listing.add_argument("--five-star", action="store_true", help="只列出含五星的套装")

    drop = commands.add_parser("drop", help="从指定秘境模拟一件或多件五星掉落")
    drop.add_argument("--domain", required=True, help="秘境名称或 ID")
    drop.add_argument("--count", type=int, default=1)
    drop.add_argument("--four-line-probability", type=float)
    drop.add_argument("--seed", type=int)

    create = commands.add_parser("create", help="自选生成一件五星圣遗物，省略的词条按模型抽样")
    _generation_options(create)

    enhance = commands.add_parser("enhance", help="读取一件圣遗物并强化")
    enhance.add_argument("file", help="单件 JSON 的路径，或 - 读取标准输入")
    enhance.add_argument("--to", dest="to_level", type=int, required=True)
    enhance.add_argument("--force", action="append", default=[], metavar="LEVEL:STAT:TIER")
    enhance.add_argument("--seed", type=int)
    for name, help_text in (
        ("inspect", "读取圣遗物，查看当前数值、预览及强化历史"),
        ("reset", "回到 +0，并保留初始词条和第 4 条预览"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("file", help="单件 JSON 的路径，或 - 读取标准输入")
    equip = commands.add_parser("loadout", help="合计装备提供的属性并列出已生效套装效果")
    equip.add_argument("files", nargs="+", help="一至五件圣遗物的 JSON，部位必须各不相同")
    character_query = commands.add_parser("characters", help="按元素、武器和地区筛选角色")
    character_query.add_argument("--element")
    character_query.add_argument("--weapon")
    character_query.add_argument("--region")
    _inventory_parser(commands)
    _data_parser(commands)
    _damage_parser(commands)
    _models_parser(commands)
    _party_parser(commands)
    farming = commands.add_parser("farming", help="精确计算不同秘境的单件提升概率和期望收益")
    farming.add_argument("file", help="JSON 路径，或 - 读取标准输入")
    farming.add_argument("--profile", help="从库存读取此角色或自定义配装的已装备圣遗物")
    farming.add_argument("--db", type=Path, default=Path("artifacts.sqlite3"))
    _plan_parser(commands)

    args = parser.parse_args()
    try:
        if args.command == "catalog":
            data = catalog()
            if args.five_star:
                data["sets"] = [item for item in data["sets"] if 5 in item["rarities"]]
        elif args.command == "drop":
            if args.count < 1:
                raise ValueError("件数必须大于 0")
            simulator = Simulator(args.seed)
            pieces = [
                simulator.drop(args.domain, four_line_probability=args.four_line_probability).describe()
                for _ in range(args.count)
            ]
            data = pieces[0] if args.count == 1 else pieces
        elif args.command == "create":
            pieces = [piece.describe() for piece in _generate(args)]
            data = pieces[0] if args.count == 1 else pieces
        elif args.command == "loadout":
            data = loadout([_read_artifact(path) for path in args.files])
        elif args.command == "characters":
            data = characters(element=args.element, weapon=args.weapon, region=args.region)
        elif args.command == "inventory":
            data = _inventory_command(args)
        elif args.command == "data":
            data = _data_command(args)
        elif args.command == "damage":
            data = _damage_command(args)
        elif args.command == "models":
            data = _models_command(args)
        elif args.command == "party":
            data = _party_command(args)
        elif args.command == "farming":
            data = _farming_command(args)
        elif args.command == "plan":
            data = _plan_command(args)
        else:
            artifact = _read_artifact(args.file)
            if args.command == "enhance":
                artifact = Simulator(args.seed).enhance(artifact, args.to_level, overrides=_overrides(args.force))
            elif args.command == "reset":
                artifact = artifact.reset()
            data = artifact.describe()
        _write_json(data, args.output)
    except (OSError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
