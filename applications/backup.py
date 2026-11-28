from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quick_check(path: Path) -> dict:
    if not path.is_file():
        return {"ok": False, "reason": "missing_database"}
    try:
        with closing(sqlite3.connect(path)) as db:
            row = db.execute("PRAGMA quick_check").fetchone()
        return {
            "ok": row is not None and row[0] == "ok",
            "reason": None if row is not None and row[0] == "ok" else str(row[0]),
        }
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "reason": str(exc)}


class PersistenceBackup:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.accounts_db = self.root / "accounts.sqlite3"
        self.cache_db = self.root / "calculation_cache.sqlite3"

    def health(self) -> dict:
        return {
            "schema_version": 1,
            "root": str(self.root),
            "accounts": _quick_check(self.accounts_db),
            "cache": _quick_check(self.cache_db),
            "wal_files": [
                str(path.name)
                for path in self.root.glob("*.sqlite3-wal")
                if path.is_file()
            ],
            "status": (
                "healthy"
                if _quick_check(self.accounts_db)["ok"]
                and (
                    not self.cache_db.exists()
                    or _quick_check(self.cache_db)["ok"]
                )
                else "degraded"
            ),
        }

    def create(self, destination: str | Path) -> dict:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = destination / f"genshin-analyzer-{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for name, source in (
            ("accounts.sqlite3", self.accounts_db),
            ("calculation_cache.sqlite3", self.cache_db),
        ):
            if not source.is_file():
                continue
            target = backup_dir / name
            with closing(sqlite3.connect(source)) as source_db:
                with closing(sqlite3.connect(target)) as target_db:
                    source_db.backup(target_db)
            check = _quick_check(target)
            if not check["ok"]:
                raise RuntimeError(f"备份数据库校验失败：{name}：{check['reason']}")
            files[name] = {
                "sha256": _sha256(target),
                "bytes": target.stat().st_size,
            }
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "backup_dir": str(backup_dir),
            "manifest": manifest,
        }

    def restore(self, backup_dir: str | Path, *, replace: bool = False) -> dict:
        if not replace:
            raise ValueError("恢复需要 replace=True；不会静默覆盖现有持久化")
        backup_dir = Path(backup_dir)
        manifest_path = backup_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("备份目录缺少 manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("备份 manifest 不是有效 JSON") from exc
        if manifest.get("schema_version") != 1:
            raise ValueError("不支持的备份版本")
        restored = []
        for name, metadata in manifest.get("files", {}).items():
            if name not in ("accounts.sqlite3", "calculation_cache.sqlite3"):
                raise ValueError(f"备份包含未知文件：{name}")
            source = backup_dir / name
            if not source.is_file():
                raise ValueError(f"备份缺少数据库文件：{name}")
            if _sha256(source) != metadata.get("sha256"):
                raise ValueError(f"备份校验失败：{name}")
            check = _quick_check(source)
            if not check["ok"]:
                raise ValueError(f"备份数据库损坏：{name}：{check['reason']}")
            target = self.root / name
            staged = target.with_name(target.name + ".restore")
            with closing(sqlite3.connect(source)) as source_db:
                with closing(sqlite3.connect(staged)) as target_db:
                    source_db.backup(target_db)
            staged.replace(target)
            restored.append(name)
        return {
            "schema_version": 1,
            "restored": restored,
            "health": self.health(),
        }
