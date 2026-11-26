"""Call the pinned Genshin Optimizer Pando bundle through a local JSON process."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from shutil import which
from typing import Mapping


_RUNTIME = Path(__file__).with_name("go_damage")


def damage_engine_info() -> dict:
    """Return the pinned upstream revision, build dependencies and bundle checksum."""
    return json.loads((_RUNTIME / "manifest.json").read_text(encoding="utf-8"))


def _invoke(action: str, payload: Mapping[str, object], node: str | Path) -> dict:
    executable = which(str(node))
    if executable is None:
        raise ValueError("找不到 Node.js 24 或更新版本，请安装后加入 PATH，或显式传入 node 路径")
    request = json.dumps({"action": action, "payload": dict(payload)}, allow_nan=False)
    try:
        process = subprocess.run(
            [executable, str(_RUNTIME / "bridge.mjs")],
            input=request, capture_output=True, encoding="utf-8", timeout=30, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Genshin Optimizer 伤害计算超时（30 秒）") from exc
    if process.returncode != 0:
        raise ValueError(f"Genshin Optimizer：{process.stderr.strip() or process.returncode}")
    result = json.loads(process.stdout)
    if not isinstance(result, dict):
        raise ValueError("Genshin Optimizer 返回了非对象结果")
    return result


def calculate_damage(payload: Mapping[str, object], *, node: str | Path = "node") -> dict:
    """Evaluate named single-character attacks with the upstream Pando calculator."""
    return _invoke("calculate", payload, node)


def list_damage_formulas(character: str, *, node: str | Path = "node") -> dict:
    """Return upstream attack names and registered character conditions."""
    return _invoke("formulas", {"character": character}, node)
