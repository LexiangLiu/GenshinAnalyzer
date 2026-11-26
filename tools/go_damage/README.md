# Genshin Optimizer 伤害后端

ArtifactSimulator 本体使用 Python 3.10+ 和 SQLite。伤害调用时由 Python 通过标准输入输出启动本机 Node.js 24+，把 JSON 转成 Genshin Optimizer 的 TagMap 条目，再用 Pando 读取对应伤害节点。发行包随附已构建的 `bridge.mjs`、源码版本清单和许可证。查询静态图鉴、库存或运行圣遗物概率模拟时无需启动 Node.js。

## 上游与依赖

固定 Genshin Optimizer **10.40.0**，提交 `916e46d14d1031c9188a6122f045f3308b0a1f37`，MIT。上游是 Node.js 24、Yarn 3.4.1 和 Nx 工作区。TypeScript 中的 `@genshin-optimizer/gi/formula` 与 `@genshin-optimizer/pando/engine` 由上游 `tsconfig.base.json` 解析到仓库内部路径。公开 npm 查询未找到可用的独立 GI 公式包。构建复用固定提交的公式、已提交的公式目录及 `gi/stats` 已生成的技能倍率和成长数据。 [固定源码](https://github.com/frzyc/genshin-optimizer/tree/916e46d14d1031c9188a6122f045f3308b0a1f37) · [那维莱特角色公式](https://github.com/frzyc/genshin-optimizer/blob/916e46d14d1031c9188a6122f045f3308b0a1f37/libs/gi/formula/src/data/char/Neuvillette.ts)

实际打包依赖包括 `gi/formula`、`gi/stats`、`gi/consts`、`pando/engine`、`game-opt/engine`、`game-opt/formula` 和 `common/util`。构建工具为 esbuild 0.19.2（MIT），npm 依赖 fflate 0.8.3（MIT）随包解析。确切版本及校验见 [package-lock.json](package-lock.json) 和 [运行清单](../../artifact_sim/damage/go_damage/manifest.json)。两份发行时使用的许可证副本随引擎打包。 [Genshin Optimizer MIT](../../artifact_sim/damage/go_damage/LICENSE-genshin-optimizer.txt) · [fflate MIT](../../artifact_sim/damage/go_damage/LICENSE-fflate.txt)

## 调用

从 ArtifactSimulator 目录执行。

```powershell
python -m artifact_sim damage formulas Neuvillette
python -m artifact_sim damage evaluate examples/neuvillette-damage.json
```

```python
import json
from pathlib import Path
from artifact_sim import calculate_damage, list_damage_formulas

formulas = list_damage_formulas("Neuvillette")
request = json.loads(Path("examples/neuvillette-damage.json").read_text(encoding="utf-8"))
result = calculate_damage(request)
print(result["attacks"])
```

| 请求字段 | 含义 |
| --- | --- |
| `character`、`level`、`ascension`、`constellation` | 上游角色键、等级、突破阶段和命座。 |
| `talents` | `auto`、`skill`、`burst` 是升级后的基础天赋等级。命座提升由角色公式计算。 |
| `bonus_stats` | 角色或武器基础属性之外的加成。例如 `hp: 25000` 是额外固定生命值，`hp_: 0.2` 是额外 20% 生命值，`critRate_: 0.55`、`hydro_dmg_: 0.466` 分别是额外 55% 暴击率和 46.6% 水伤。结果中的 `panel` 是算出的面板。 |
| `conditions` | 由 `damage formulas` 列出的角色条件。那维莱特的 `a1Stacks` 为「遗龙之荣」层数，`a4Hp` 为实际生命值高于 30% 的百分点数，取 0–50。 |
| `weapon` | 可选武器。字段为 `key`、`level`、`ascension`、`refinement`、`conditions`。示例省略武器条目。 |
| `enemy` | 可选。默认 90 级、基础抗性 0.1。可传 `resistances` 按元素覆盖，或传小数表示的 `defense_reduction` 与 `defense_ignore`。 |
| `mode` | `average` 为计入暴击概率的期望值，`noncrit` 为非暴击值。 |
| `attacks` | 当前角色的上游伤害公式键列表。单次请求可以计算多种攻击。 |

示例的那维莱特在 C2、普攻天赋 10、90 级且水抗 10% 的敌人条件下，衡平推裁单跳期望 **15,205.57**，战技 **13,370.10**，元素爆发主击 **23,133.50**。重击每一跳、水瀑等分段伤害有各自的公式键。

接口以单角色和无反应场景为范围，角色攻击覆盖以固定提交的公式目录为准。例如该提交的米提亚角色表只有第一段普攻公式。该提交的强制暴击分支使用了封顶暴击率作为倍率项，目前公开的计算模式为期望和非暴击。 [米提亚公式表](https://github.com/frzyc/genshin-optimizer/blob/916e46d14d1031c9188a6122f045f3308b0a1f37/libs/gi/formula/src/data/char/Mitya.ts) · [上游通用伤害分支](https://github.com/frzyc/genshin-optimizer/blob/916e46d14d1031c9188a6122f045f3308b0a1f37/libs/gi/formula/src/data/common/dmg.ts)

## 重建与升级

在独立目录获取固定提交。从 ArtifactSimulator 根目录安装锁定的构建依赖，再从上游源码生成独立 Node.js 文件。上游已提交的生成数据满足构建，流程无需启动网站或初始化游戏数据子模块。

```powershell
$source = 'D:\Projects\genshin-optimizer-source'
git clone --filter=blob:none --no-checkout https://github.com/frzyc/genshin-optimizer.git $source
git -C $source sparse-checkout set libs/common libs/game-opt libs/gi libs/pando
git -C $source checkout --detach 916e46d14d1031c9188a6122f045f3308b0a1f37
npm.cmd ci --prefix tools/go_damage
node tools/go_damage/build.mjs $source
python -m unittest discover -s tests -v
```

更新时先核对新提交的内置数据、公式键、运行条件和许可证。更新 `upstream.json`，若构建依赖发生变化则同步锁文件。用干净的上游 checkout 重建，核对生成清单和两份许可证，用那维莱特示例及命座、天赋对照测试复核数值变化。
