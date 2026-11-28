# 原神五星圣遗物模拟

Python 3.10 或更新版本。运行只需要 Python 标准库。以下命令从本目录执行。

## 模块和调用入口

`artifact_sim/` 仅保留圣遗物模拟器公开 API 门面与命令行入口。其他可独立复用的功能按职责拆分为仓库根目录下的平级模块：

| 模块 | 职责 |
| --- | --- |
| `core/` | 圣遗物模型、随机模拟、评分、配装属性和副词条概率 |
| `data/` | 游戏套装/秘境数值、离线图鉴快照及其查询 |
| `damage/` | 伤害公式桥接、配装目标、伤害模型和角色模型目录 |
| `inventory/` | SQLite 库存管理和旧版 JSON 转换 |
| `optimization/` | 单角色、多角色及武器与圣遗物联合优化 |
| `planning/` | 刷取收益、树脂估算及单角色/多角色计划 |

原 `artifact_sim` 顶层子模块导入路径继续作为兼容入口；新代码可直接从上表中的职责包导入。

掉落、强化、评分和配装计算可以直接从 Python 调用，无需启动服务或打开数据库。推荐的公开入口是 `artifact_sim` 包。其他项目可以在自己的环境中运行 `python -m pip install D:\Projects\Genshin\ArtifactSimulator` 安装，也可以调用安装后的 `artifact-sim` 命令。

## 实验效果验收界面

仓库自带一个标准浏览器访问的本地 Web 应用（不是桌面 GUI 或桌面打包应用），用于串联账号生成、单角色/多角色配装优化和秘境树脂规划。页面由 `tools/acceptance_ui.html` 提供 HTML/CSS/JavaScript，本地标准库 HTTP 服务负责调用现有 Python 能力；直接调用 `tools/simulate_account.py`、`artifact_sim party optimize` 和 `artifact_sim party plan`，不引入另一套领域逻辑。

离线图鉴按同时存在的天赋与命座资料记录提供旅行者·风、岩、雷、草、水、火、冰七种形态；`旅行者`、元素形态的中文/英文名及资料键均可查询。模拟账号只生成一个旅行者身份，空/荧外观由随机种子选择，元素形态也可按种子随机或在页面及 `--traveler-element anemo` 中指定。旅行者的武器、等级、天赋和当前形态命座保留在账号详情中；所有元素形态共用一个背包配装身份，多角色优化拒绝重复选择。七个形态均有由固定 Genshin Optimizer 伤害桥校准的伤害模型，只有当前形态进入候选。

奇偶（Manekin / Manekina）作为一个逻辑角色，只随机生成一种性别外观并只占一个账号/配装身份；同一队伍不能同时选择男女外观。其 Adaptive 元素随当前旅行者形态变化。图鉴与模拟详情列出普攻、战技、爆发及被动能力、无命座、等级/天赋模拟值和无需突破材料/摩拉等状态。角色基础记录来自 [genshin-db 5.2.15](https://github.com/theBowja/genshin-db/tree/73d57660053377e55beb12e8f627aabd7ef4cc0d)（Manekin ID 10000117、Manekina ID 10000118）；离线战斗参数从 [AstaPS Resource 7.2](https://github.com/MeChen618/AstaPS-Resource/tree/f2800e32599f32cde76ae0b4b93ea30b75651e59) 的原始 `AvatarSkillDepot`、`AvatarSkill` 和 `ProudSkill` 表提取，并与 [genshin-data](https://github.com/dvaJi/genshin-data/tree/4c8484ff9c6270db90ff581d7a584b06d035829d) 男女角色快照的 15 级普攻、战技和爆发倍率逐项交叉核对。元素与 depot ID 的对应关系另以 [固定版 raw-casts 映射](https://github.com/mixuanda/genshin-elemental-reaction-sim/tree/4a70bdfdd60eecc69a9e3e23ae540c77fd17dfbd/packages/game-data/sources/manekin-raw-casts) 标记；该映射自身标注为 provisional，构建时逐一核对其 14 个外观/元素 depot ID 与主快照原始表。快照保存三类技能的等级 1–15 参数、元素技能双充能/冷却/附魔时长、爆发冷却/能量/领域参数（领域进入伤害每 0.5 秒至多触发一次）以及三个被动的触发解锁与数值；男女角色及风、岩、雷、草、水、火、冰七元素形态均可追溯到各自 depot/skill ID。来源提交、文件 SHA-256、逐项交叉核对状态和模型状态记录在 `data/reference_data/manifest.json`。可以用固定来源的原始表和交叉核对 JSON 重建补充快照：

```powershell
python tools\build_manekin_combat_reference.py --source-dir <AstaPS ExcelBinOutput 目录> --crosscheck-dir <genshin-data 英文 characters 目录> --element-mapping-file <固定版 definitions.json>
```

倍率数据已补齐，但当前固定 Genshin Optimizer 10.40.0（commit `916e46d14d1031c9188a6122f045f3308b0a1f37`）没有 Manekin/Manekina 公式节点，因此七种形态都明确标记为未校准，不加入伤害公式目录或配装评分；不会用通用公式冒充上游校准结果。当前元素形态的实际天赋等级倍率会显示在角色详情。千星奇域模式下的换装和自由切换只作能力摘要记录，不模拟该模式。

```powershell
python -m tools.acceptance_ui
```
可通过环境变量或命令行指定持久化目录：

```powershell
python -m tools.acceptance_ui --state-dir C:\path\to\genshin-analyzer-state
```

`--state-dir` 下保存账号快照、养成目标、方案和计算缓存；本次模拟账号的角色和圣遗物仍保存在临时目录，关闭服务后清理。未提供参数时使用 `~/.genshin-analyzer/planning`。

持久化 SQLite 使用 WAL、忙等待和 schema 版本迁移。旧表启动时会补齐缺失列与索引，不要求手工迁移。并发写入使用 10 秒 busy timeout；缓存只复用相同快照、目标、库存和引擎输入。

结构化账号导入只接受 `manual` 或 `legacy_import` 来源，不提供真实官方账号同步。活动配置只接受带 `source_ref`、有效期、置信度和验证状态的结构化输入，过期或未验证规则不计入收益。

## 发布与维护清单

## 性能基准

```powershell
python tools/run_acceptance_suite.py
python tools/benchmark_growth.py --repeats 5
python tools/run_preflight.py
python tools/release_checklist.py
```

基准输出包含公式覆盖、方案计算/持久化和备份恢复的本机耗时，不设硬件 SLA，不声称配装或 Monte Carlo 的真实生产吞吐量。

- 发布前运行 `python tools/run_acceptance_suite.py`，并保留完整测试输出。
- 发布前运行旧 SQLite 迁移测试、并发保存测试、live HTTP 全工作流测试和持久化重启测试。
- 公式目录更新时同时更新来源提交、生成时间、校准精度和覆盖审计；未校准公式不得进入可组合评分。
- 图鉴快照更新时核对 manifest SHA-256 与分类计数，不通过手工猜测补齐缺失字段。
- 活动配置必须保留来源、有效期、置信度和验证状态；过期、未验证或低置信度配置只降级。
- `--state-dir` 指向持久目录前先备份 SQLite 和 WAL 文件；恢复时使用新进程启动，不删除旧快照或方案。
- 恢复是显式操作：备份 manifest 会校验数据库版本、SHA-256 和 `quick_check`，应用不会静默覆盖现有持久化。
- 发布不自动执行真实账号同步、实时活动抓取、合并、推送、公开发布或生产部署；这些操作需要明确授权与可靠凭据。

使用 uv 管理 Python 时，也可在仓库根目录执行 `uv run --python 3.12 python -m tools.acceptance_ui`。启动后在 Chrome、Edge 或其他浏览器打开终端打印的 `http://127.0.0.1:8765/` 地址即可使用，服务只监听本机；按 Ctrl+C 关闭。`--port 0` 可使用系统分配的可用端口，`--no-browser` 可禁止自动打开浏览器。账号数据库和请求结果写入临时目录，服务关闭时清理。

在界面中配置角色数、圣遗物数和生成种子，选择一个或多个优化候选角色，设置优化种子与搜索节点上限，再运行优化。角色数量上限从离线角色图鉴动态读取，可一次生成图鉴内全部角色；账号全量角色与武器照常生成，配装优化候选仍沿用模拟器预设目标阵容，避免把全角色数量误当作一个超大联合优化队伍。圣遗物数量为 20–1800；1800 是《原神》版本 5.0 官方更新说明公布的背包容量上限，前端、服务端和账号生成器共用此值。优化结果可继续提交给秘境规划；规划阶段会校验复算配装与上一步结果完全一致。

每次配装会同时展示两种结果：一是按圣遗物当前实际强化等级求出的最优配装；二是把库存中未满级圣遗物的剩余强化按游戏数据中的词条/档位概率计算期望副词条（初始三词条会包含 +4 解锁第四词条的期望），再代入同一伤害模型重新求出的预期满级配装。预期配装会标出被选中的候选、对应角色与部位、替换的当前最佳件和目标值变化；目标变化按角色权重汇总各角色“预期伤害 / 当前最优伤害”的比例。这是基于期望属性的潜力估算，不保证实际强化结果，也不是对随机强化后伤害分布的精确积分。多角色模式仍由 party optimizer 对共享库存联合分配，秘境规划则继续使用当前等级的实际最优配装。

优化结果的每个角色卡片可展开查看按因子分行的中文评分式、实际天赋等级、当前面板和已启用套装效果，以及中文来源、攻击条件、校准与模型假设；“本次优化实际代入值”会列出每项实际数值、单位、数据来源及逐项展开的伤害/暴击系数，并给出乘积算式。该算式使用本次优化的原始面板、激活套装标记、指定天赋和模型倍率；服务端会将重算分数与优化器结果核对，不一致或缺少输入时不显示伪造的数值。敌人防御/抗性若仅作为模型标定条件，会明确标注且不重复乘算。内部属性名、模型键或套装 ID 不会展示给用户；无法取得本地套装名称时显示“未命名效果”，没有结构化评分因子的模型不展示原始代码表达式。“本次评分模型”标记与实际参与评分的模型一致。目录模型随模型数据保存天赋 1–10 级的上游倍率比例；天赋 10 级保持原标定分数。选用其他固定四件套时，注册表中与该攻击类别/元素匹配且已有定义的套装效果会接入评分。普通武器只把白值与副属性作为面板输入，未建模的武器被动会明确注明不计入；那维莱特模型展示其已建模武器重击加成。其他参考模型和上游攻击节点仍分别标注，不会暗示参与本次评分，也不会伪造缺失公式。

每位角色可独立选择默认公式、单个已校准公式，或组合多个该角色的已校准公式；组合方式支持“相对表现加权”和“原始伤害加权”。相对模式先将各公式伤害除以同角色、同等级与武器、无圣遗物且无条件套装效果的裸面板参考伤害，再按权重合并；原始模式直接加权伤害值，权重可表达攻击循环占比。两种模式的权重都必须是有限非负数，后端会归一化至总和 1。结果逐公式展示实际伤害、参考值、归一化值、权重与贡献；多个角色分别配置，仍共享库存。仅通过高/中精度可靠性门槛的目录公式可参与组合；目录覆盖信息会指出没有可靠普攻、战技或爆发节点的角色/类别。组合目标仅在所有正权重分项都能证明单调时保留 exact 搜索标记，否则不会声称精确。

“树脂投入的期望配装”可在单角色优化后估算多个自定义预算下的整套最优配装期望。每档独立抽样未来秘境库存，每 20 树脂按一件五星圣遗物估算，并按离线词条概率随机强化至 +20；起点可选当前库存（未满级件按期望满级属性投影）或空库存。结果给出优化后面板、模型目标及每个选中公式的期望、相对刷前基线的期望变化、近似 95% 抽样误差区间和受固定四件套约束影响的可行样本比例，不展示抽样装备。默认预算为 0/200/600/1200 树脂，按 180 自然树脂/天约为 0/1.1/3.3/6.7 天；每档默认 16 次抽样，可调为 8–64 次。该结果是“随机未来库存上重新优化后的整套最优值”期望，不是将单件期望属性相加，也不是精确概率积分。模型暂按每 20 树脂恰好一件五星，未计额外掉落、强化资源成本；置信区间只反映蒙特卡洛误差，不包含配装搜索达到节点上限造成的误差。预算最多 8 档、每档最多 4000 树脂，最多执行 96 次配装搜索。

多角色优化后还可比较“账号刷本优先级”：对当前选择的角色和各自已选公式，先按各角色模型裸面板目标值归一化，再按角色权重汇总账号目标；每个秘境、预算的每个样本都将随机掉落加入同一共享库存并重新联合配装，因此角色贡献不会把同一件圣遗物重复分配。可选当前全库存或从零开始；空库存允许残缺配装并使用实际激活的套装效果，逐角色报告目标四件套达成概率。默认比较 200/600 树脂（按每天 180 自然树脂约 1.1/3.3 天），支持自定义多个秘境、预算、8–64 次抽样和账号提升达标阈值。结果按预期账号提升/百树脂排序，展示预期属性与公式伤害变化、角色贡献、抽样均值的近似 95% 区间、单次结果经验 P10–P90、达标概率及 Wilson 区间。每 20 树脂按一件五星并强化至 +20；未满级现有库存按期望满级属性投影。所有风险区间只反映概率抽样误差，不覆盖库存争用启发式搜索误差，故不得视为精确搜索保证；账号规划最多 8 个秘境、8 档预算、64 次抽样，且“秘境数 × 预算数 × 抽样数”不得超过 192 次抽样联合搜索，另需一次基线优化。

生成账号后可在“角色与库存详情”按角色、武器、圣遗物切换查看并搜索；列表由服务端分页，每页最多 24 条，详情在点击后打开。角色显示图鉴字段和模拟等级、命座、天赋及已分配武器；武器显示图鉴基础攻击力/副属性、模拟等级与精炼及装备角色；圣遗物显示套装、部位、星级、等级、主副词条和当前装备档案。优化结果中被选中的圣遗物及预期强化候选也可直接打开详情。模拟数据没有保存角色或武器突破阶段，因此详情不会推算或伪造其当前面板属性；若武器记录仅凭名称对应到多个图鉴条目，则只展示它们共有的字段并注明缺少唯一 ID。

## 命令行

```powershell
Set-Location D:\Projects\Genshin\ArtifactSimulator
python -m artifact_sim catalog --five-star
python -m artifact_sim -o drops.json drop --domain 逆悬的冰河 --count 10 --seed 73
python -m artifact_sim -o piece.json create --set 15048 --slot flower --lines 3 --sub CRIT_RATE:4 --sub CRIT_DMG:4 --sub ATK_PERCENT:4 --sub ENERGY_RECHARGE:4
python -m artifact_sim inspect piece.json
python -m artifact_sim -o piece-4.json enhance piece.json --to 4 --seed 9
python -m artifact_sim -o piece-20.json enhance piece-4.json --to 20 --seed 9
python -m artifact_sim reset piece-20.json
python -m unittest discover -s tests -v
python tools/run_acceptance_suite.py
```

示例生成的「炉火融炼之心」生之花在 +0 预览第 4 条元素充能效率 **6.48%**，与游戏界面保留一位小数后的 6.5% 对应。强化至 +4 时，`pending_substat` 变为 `null`，同一条数值进入 `active_substats`，历史记为 `activate`。三词条件在 +8、+12、+16、+20 强化已有词条，四词条件从 +4 开始强化已有词条。强化可停在任意整数等级。

`create` 支持指定套装、部位、主词条、初始激活条数和副词条。档位从 1 到 4，省略条目或档位时按规则抽样。三词条自选中，第 4 个 `--sub` 指定预览。标准增强按概率抽样，`enhance --force 8:CRIT_RATE:4` 可用于定向试算。三词条在 +4 的激活禁止被覆盖。

同一个命令使用相同的 `--seed` 可复现结果。种子控制本次命令中的随机抽样，初始四条和每次已完成强化随存档保存。按步调用 Python 接口时，可复用同一个 `Simulator` 实例以连续使用随机数流。百分比以百分点计，如 6.48 表示 6.48%，而固定值以原单位计。

`loadout a.json b.json` 汇总各部位主词条、已激活的副词条和常驻面板类套装属性。条件性效果以文本返回。评分默认使用攻击与双暴预设，另有攻击、双暴、防御、生命、充能、精通预设，支持组合。

### 伤害计算

伤害模块使用 Node.js 24 或更新版本。安装本项目后即可通过 Python 或命令行调用，运行时使用已打包的引擎和技能倍率。

```powershell
python -m artifact_sim damage info
python -m artifact_sim damage formulas Neuvillette
python -m artifact_sim damage evaluate examples/neuvillette-damage.json
```

Python 入口为 `calculate_damage`、`list_damage_formulas` 和 `damage_engine_info`。请求字段、默认敌人设定、公式覆盖和上游更新步骤见 [伤害模块说明](tools/go_damage/README.md)。

### 角色伤害模型目录

`character_models.json` 当前包含 386 个由本机 Genshin Optimizer 10.40.0 桥接标定的单段公式，覆盖 129 个普通角色/旅行者元素形态；另有手写的那维莱特重击模型。每个角色明确保留一个 curated/default 公式，并尽量提供普攻、战技、爆发代表节点；每项公式分别标定与验证，不把上游仅有名称的节点当成可评分模型。17 个角色的 `coverage` 记录标记出缺少可靠节点的类别及原因，UI 与 API 不允许其进入组合评分。当前旅行者七元素形态均有普攻、战技、爆发模型，分别使用上游 `TravelerAnemo`、`TravelerGeo`、`TravelerElectro`、`TravelerDendro`、`TravelerHydro`、`TravelerPyro`、`TravelerCryo` 公式，并按一个逻辑角色互斥；草元素模型精度为 medium（最大相对误差约 3.56%），其余为 high。奇偶记录了能力但没有目录模型，因而不可评分；训练人偶也没有目录模型。每个目录模型在 90 级、C0、天赋 10/10/10、无武器、90 级无抗性木桩下标定；生成器另外逐级查询对应伤害天赋 1–10 级的倍率比例，供优化请求动态传入实际等级：

- 伤害节点：默认公式优先使用策划表或主流攻略指定的主伤害段；其他节点在排除命座、被动和锁定攻击后，分别选择普攻、战技、爆发类别中基准伤害最高的有效节点；
- 面板系数：对生命、攻击、防御、元素精通、元素充能效率做有限差分；
- 增伤与暴击：用 +50% 元素伤害探针反解基础增伤，用期望/非暴击比反解技能暴击加成；
- 套装：按角色定位选择四件套，并把未包含在静态面板里的条件效果写成 `SET_<id>_2/4` 项；
- 校验：在 12 组随机圣遗物级属性扰动上对比桥接输出，记录 `accuracy` 与 `max_relative_error`。

```powershell
python -m artifact_sim models list --element PYRO
python -m artifact_sim models list --accuracy high
python -m artifact_sim models show 阿蕾奇诺
python tools/calibrate_character_models.py --workers 12
```

`models show` 会给出表达式、假设、标定探针、误差和来源。高精度模型误差 ≤2%，中等 ≤10%；低精度或未校验模型保留作诊断但不可参与多公式综合评分。目录模型是单段、无元素反应、无队伍增益的标量化模型，优化目标是该角色的个人伤害；治疗、护盾、增益和减抗等辅助价值不在目标内。自动模型不包含武器被动，调用方提供的武器白值与副属性作为面板输入。已建模的条件套装效果按选择的目标套装和伤害类别接入；其他效果不会自动推导。胡桃模型固定使用精一护摩之杖（含 HP% 与生命转攻击），那维莱特模型固定使用精一万世流涌大典，且重击武器加成与实际天赋等级会作为因子输入。

标定使用固定提交的 Genshin Optimizer 公式与倍率。伤害公式结构、暴击与防御/抗性乘区参考 [Genshin Damage Formula](https://www.genshindamagecalculator.com/guides/damage-formula/)；套装定位参考 [Game8 All Best Builds](https://game8.co/games/Genshin-Impact/archives/530535)、[Game8 Arlecchino](https://game8.co/games/Genshin-Impact/archives/382103)、[Game8 Mavuika](https://game8.co/games/Genshin-Impact/archives/461999) 和 [Prydwen Neuvillette](https://www.prydwen.gg/genshin-impact/characters/neuvillette)。

### 圣遗物刷取收益

```powershell
python -m artifact_sim farming examples/neuvillette-farming.json
```

示例以那维莱特、万世流涌大典和一套生成的逐影猎人配装为输入。目标套装为逐影猎人，后端自动选中罪祸的终末。该秘境也掉落黄金剧团，当前配装里黄金剧团水伤杯只有当它能替换散件水伤杯并提升 Ω 时才计入提升。例中按精一武器满层、四件套三层暴击模拟重击的相对收益，固定相同的技能倍率和敌人条件。五星件数采用每 20 树脂恰好一件的示例分布。

- `equipped` 接受零至五个圣遗物状态或 `describe()` 输出。同部位只能有一件。要用存档里的装备，移除 JSON 的 `equipped`，执行 `python -m artifact_sim farming request.json --profile 那维莱特`。Python 使用 `analyze_farming(artifacts, **request)`。
- `character`、`weapon` 的 `id` 可填名称或 ID，同时填写 `level`，在突破等级可加 `ascension`。角色和武器的常规等级属性来自本地图鉴。武器特效、技能、命座或队伍条件按需要由 `bonus_stats` 或目标公式表达。例如 `HP_PERCENT=16` 表示面板增加 16 个百分点。
- `objective` 为要最大化的 Ω，支持 `+`、`-`、`*`、`/`、`min`、`max`。`HP`、`ATK`、`DEF` 为最终面板值，`BASE_HP` 等是白值，`FLAT_HP` 等是额外固定值。暴击等百分比属性以百分点计。`SET_15031_4` 在逐影猎人四件生效时为 1，其余为 0。配装的常驻套装面板属性沿用 `loadout` 的规则。
- `target_four_piece_set` 指定目标套装，允许任意部位作为散件。每件候选换装后都必须满足四件套，当前应已有三件以上目标套装。省略该字段时，默认保留已激活的套装效果，可用 `preserve_set_bonuses: false` 放宽。`main_stats` 可限制各部位可用的主词条，省略时使用游戏权重表中的全部主词条，指定空列表可跳过该部位。
- 指定 `target_four_piece_set` 时，默认评估掉落该套装的秘境。`domains` 可显式指定需要评估的秘境，输出的 `drop_sets` 列出各秘境的掉落套装。预算填 `drops` 表示固定的五星件数，或填 `resin` 和每 20 树脂的五星件数分布 `five_star_per_claim`。如 `{"1": 0.94, "2": 0.06}` 表示一次结算有 94% 概率给一件、6% 概率给两件，仅用于说明输入格式。件数分布由调用方提供，秘境结算及同次结算内的掉落按独立模型计算。
- 输出同一预算下每个秘境和部位的提升概率、期望 Ω、每树脂收益及最终配装副词条期望。`report_substats` 可指定要报告的副词条，含公式未用到的属性。默认报告公式涉及的副词条。可选的 `min_gain_pct_per_100_resin` 是每 100 树脂能接受的最低 Ω 相对增幅，若无秘境达到该门槛，推荐秘境为空。`min_upgrade_pct` 只统计相对 Ω 提升达到该百分比的候选，默认 0 表示任意正提升。

副词条使用带权无放回分布，初始三条和四条分别对应 +20 前额外强化已有词条 4 次和 5 次。设单件的配装目标分布为 `F(x)`，一轮结算的五星件数为 `K`，则结果的累计概率为 `E[F(x)^K]`。多轮结算对该值取轮数次幂，再由离散分布求期望。部位结果分别与当前配装比较，整套结果对同一批掉落取最高 Ω。相同 Ω 时优先保留原配装，多个严格优于原配装的掉落同分时取最先掉落的一件。计算采用离散枚举，概率输出按浮点精度舍入。

当前范围是一次至多采用一件新掉落，候选都按 +20 结算。多件联换、库存重配、强化资源与合成成本暂未纳入。每个秘境单独花费指定预算，结果可直接横向比较。概率和运行时间随目标属性及候选主词条的数量变化。算法和现有工具参考 [Artifact Sub Math](https://library.keqingmains.com/assets/files/ArtiSubMath-v10_1-0c25f059a10d40502b70fc546bdd2759.pdf) 及 [Genshin Artifact Farming Calculator](https://github.com/grumd/genshin-artifact-farm-calculator)。

### 单角色配装与刷取计划

`plan` 把“库存最优配装”和“继续刷取期望树脂”合并为一个单角色流程。它支持手写模型和 `character_models.json` 中的标定模型；省略 `--model` 时使用角色默认模型。目录模型的角色等级、突破和命座仍固定在 90 级、突破 6、C0；`party` 请求可按实际天赋等级 1–10 缩放，且那维莱特重击模型按实际普攻天赋等级计算。`plan --formula-objective '<JSON>'` 可指定单公式或相对/原始加权组合；队伍优化 JSON 在每个 `members[]` 项中使用同样的 `formula_objective` 结构。

```powershell
python -m artifact_sim plan --character 那维莱特 --db artifacts.sqlite3 --threshold 1
python -m artifact_sim plan --character 阿蕾奇诺 --weapon 赤月之形 --db artifacts.sqlite3
python -m artifact_sim plan --character 胡桃 --weapon 护摩之杖 --db artifacts.sqlite3 --all-main-stats
python -m artifact_sim plan --character 阿蕾奇诺 --db artifacts.sqlite3 --four-piece-set 15035 --formula-objective '{"mode":"relative","formulas":[{"key":"arlecchino_skill_finaldmg","weight":0.6},{"key":"arlecchino_burst","weight":0.4}]}'
```

`--weapon` 省略时不装备武器；固定武器的模型（那维莱特、胡桃）会要求对应武器。默认按模型实际使用的属性生成候选主词条，`--all-main-stats` 可放开为全部主词条，代价是更慢。那维莱特默认场景为 90 级、C0、天赋 10/9/10、90 级精一万世流涌大典、A1 零层、A4 满生命加成、武器满层、逐影猎人四件套三层暴击、暴击期望、90 级无抗性木桩和标准同级防御。公式使用 Genshin Optimizer 的 `charged_judgment` 单段伤害校准，因此绝对伤害与固定目标条件一致。

输出包括：

- 目标四件套约束下的最优五部位配装、面板和伤害；
- 目标秘境单件五星的提升概率；
- 达到提升的期望树脂、50%/90% 分位数；
- 单次提升的条件期望增幅、每 100 树脂期望增幅；
- 按部位的提升概率，以及可选当前配装的对比；
- `warnings`：未满级圣遗物、搜索达到节点上限或当前阈值下无提升等提示。

`--threshold` 是相对伤害提升阈值，默认 1%，设为 0 表示任意正提升。候选圣遗物默认按 +20 计算，只允许替换一件，并按每 20 树脂一件五星估算；圣遗物强化素材、天赋和武器升级成本暂未计入。当前最优配装受目标四件套约束，全局套装搜索和多件联换由 `party` 命令处理。为控制精确枚举规模，`plan` 和 `party plan` 的刷取评估只跟踪模型最有价值的 4 条副词条；`farming` 接口默认仍跟踪公式涉及的全部副词条，可用 `tracked_substats` 覆盖。

`plan` 要求库存中至少能组成目标四件套。默认 `artifacts.sqlite3` 若没有足够的逐影猎人件，会明确报错；可以先通过 `inventory add` 或模拟掉落准备目标套装库存。手写那维莱特模型只覆盖逐影猎人二/四件效果，传其他目标四件套会报错。第一版只计算五星掉落，不处理四星、还圣奥迹和定轨。

### 多角色联合配装

`party optimize` 在同一份库存上为多个角色分配五部位，保证每件圣遗物最多被一个角色装备，并在每个角色指定的四件套约束下最大化加权目标。

单角色底层搜索使用精确分支定界。指定目标四件套时，将可行组合划分为“5 件目标套装”或“恰好 1 件非目标套装”几类，避免重复搜索全目标套装；搜索前先生成一个可行解，并按可采纳的乐观上界优先访问候选。预排序和可行初始解只改变搜索顺序/剪枝力度，不删减解空间。单角色 `exact: true` 表示所有分支都在各自的 `max_nodes` 上限内完成；达到上限则保留当前可行最优解并标记 `exact: false`。多角色库存无争用时仍可精确；有争用时 party 层仍采用下述启发式分配，不能因单角色搜索精确而误认为联合结果全局最优。

```powershell
python -m artifact_sim party optimize examples/party-plan.json
```

请求字段：

- `members`：角色数组。每个成员支持 `character`、`model`、`weapon`、`bonus_stats`、`weight`、`target_four_piece_set`、`fixed_four_piece_set`、`name`、`profile`、`candidate_main_stats`、`level`、`ascension`、`talents`、`constellation`、`crit_mode`、`refinement` 和 `model_options`。`bonus_stats` 省略时使用模型的标定面板加成；显式提供时整体覆盖。
- Web 验收台可为每位选中角色单独指定 `fixed_four_piece_set`；它要求至少 4 件该五星套装，第五件仍可任意。目录模型会为该套装重新生成评分因子，并仅接入注册表中已有且符合伤害类别/元素条件的效果；未建模效果仍不计入并会提示。手写模型只支持其明确声明的套装效果。
- `pieces`：可选。提供圣遗物状态数组时完全不读取数据库；省略时从 `--db` 读取库存。
- `max_nodes`、`restarts`、`seed`、`max_iterations`、`normalize`：搜索参数。`normalize=true` 时每个角色先除以其独立最优伤害，避免高伤害角色支配目标。

算法先求每个角色的独立最优配装；若没有圣遗物争用，该结果就是全局最优并标记 `exact: true`。存在争用时，按多个角色顺序做贪心分配，再用坐标上升和同部位交换改进，`exact` 为 `false`。目标四件套约束必须可满足，否则报错。输出包含每个角色的配装、伤害、相对独立最优的归一化伤害、未使用圣遗物和搜索统计。

### 多角色刷取推荐

`party plan` 在联合配装之后，对每个候选秘境计算一件额外五星掉落对各角色的边际提升，并按权重汇总：

```powershell
python -m artifact_sim -o party-result.json party plan examples/party-plan.json
```

请求在 `party optimize` 基础上增加：

- `domains`：要比较的秘境名称或 ID；省略时取各角色目标四件套所属秘境的并集。
- `threshold_pct`：单件提升的相对伤害门槛，默认 1%。
- `resin_per_claim`：一次结算的树脂，默认 20。
- `score_mode`：`sum` 按加权期望相对提升排序；`max` 按单角色最高期望提升排序。
- `candidate_main_stats`：每个成员可单独限制候选主词条；省略时根据模型实际使用的属性自动生成。

每个秘境输出 `weighted_expected_gain_pct`、`best_member_expected_gain_pct`、`probability_any_member_upgrade`、`expected_resin_to_any_upgrade` 和每角色的提升概率、期望增幅、期望树脂、最佳部位。`recommended_domain_id` 为排序第一且分数为正的秘境。示例中三个角色分别需要逐影猎人、谐律异想断章和黑曜秘典，脚本会比较罪祸的终末、褪色的剧场和虹灵的净土，展示同一副本对多角色的不同价值。

一件掉落只能装备给一个角色，因此各角色期望增幅之和是队伍实际收益的上界，`best_member_expected_gain_pct` 是保守下界；两者同时输出，便于判断“补短板”还是“总收益最大”。`probability_any_member_upgrade` 按各角色提升事件独立近似，是共享掉落场景的估计值。计算使用精确的单件替换分布，成员和秘境越多越慢；示例规模约一分钟。

### 拟真账号模拟

`tools/simulate_account.py` 从空账号生成一套接近真实游戏账号的数据，并直接跑完联合配装和副本推荐：

```powershell
python tools/simulate_account.py --seed 20261126
```

默认规模为 **36 个已拥有角色**（其中 12 个参与联合优化）、**1500 件五星圣遗物**（不超过当前游戏背包上限 1800）、48 把武器和 8 个候选秘境。圣遗物按 70% 目标套装 / 30% 其他五星套装生成，主词条使用游戏权重；强化等级按 +0:40%、+20:25%、+4:12%、+8:9%、+12:8%、+16:6% 抽样，所有等级由同一随机种子驱动。+0 最常见，+20 次之；网页会同时展示配置权重和本次实际各等级件数。每个投入角色先从较早的 60% 背包池中按顺序配装，形成“当前配装”；随后使用完整背包做联合优化并比较提升。

验收 Web 的角色列表为每位优化角色提供独立的固定四件套选择。默认“自动”沿用当前角色模型的目标套装规则；选择具体套装后，优化器要求该角色至少装备 4 件该套装，第五件仍可作为任意散件。多角色可分别设定不同套装，并继续共享同一库存、禁止复用圣遗物。目录模型会把可识别的已建模套装效果作为外部因子接入；效果没有模型定义时界面会明确提示未计入。固定套装约束本身不改变多角色争用时的启发式性质。

输出目录 `examples/simulated-account` 包含：

- `account.sqlite3`：完整圣遗物库存和 12 个当前配装档案；
- `account.json`：角色库、武器库、背包统计和当前配装 ID；
- `party-request.json`：可直接重跑的 `party plan` 请求；
- `report.json` / `summary.json`：完整与精简的配装、副本收益结果；
- `report.md`：人类可读的当前/最优对比、副本排名和推荐秘境。

可用 `--characters`、`--invested`、`--artifacts`、`--domains`、`--max-nodes`、`--restarts`、`--max-iterations` 调整规模；`--skip-plan` 只生成账号和当前配装。默认每个部位只取一个标准主词条以控制精确枚举规模，`--full-main-stats` 可放开为全部候选（更慢）。精确单件枚举使端到端模拟在普通机器上需要数分钟到十几分钟；只关心一队时可把 `party-request.json` 的 `members` 裁剪到 4-8 人。

当前仓库中的示例账号使用 seed `20261127`：36 个已拥有角色、12 个投入角色、1500 件五星圣遗物、54 把武器。为控制精确枚举时间，本轮示例对主队 4 人（那维莱特、阿蕾奇诺、玛薇卡、芙宁娜）做联合优化和 3 个秘境的收益比较，提升约 3.3%–9.0%，推荐罪祸的终末；全账号 12 个投入角色的目标套装需求以椛染之庭、缘觉塔、罪祸的终末各 2 人为最高。`party-request-core8.json` 和 `party-request-full12.json` 可扩大优化范围，运行时间相应增加。

### 武器 + 圣遗物联合优化实验

`tools/weapon_experiment.py` 在模拟账号上做武器和圣遗物的二维联合分配。实验选择武器类型重叠的 4 个角色：阿蕾奇诺/雷电将军共享长柄武器池，芙宁娜/神里绫华共享单手剑池；每类武器提供专武和 4 星替代，武器全局唯一。

```powershell
python tools/weapon_experiment.py
python tools/weapon_experiment.py --no-plan   # 只做武器+圣遗物，不跑副本收益
```

流程为：

1. 对每个角色×武器组合，用该武器的基础攻击、副属性和近似被动构造模型；
2. 在完整圣遗物库存上做独立圣遗物搜索；
3. 枚举满足武器唯一的分配，按绝对队伍伤害选优；
4. 用选定的武器做联合圣遗物优化，并做一次同类型武器交换检查；
5. 可选地对结果队伍做副本收益推荐。

当前示例结果：队伍总伤害 **52,875 → 56,404（+6.67%）**。阿蕾奇诺保留赤月之形，雷电将军改用决斗之枪，芙宁娜和神里绫华保留专武。在联合结果上继续做副本收益枚举，推荐 **罪祸的终末**：加权期望 +0.273%，单角色最高 +1.061%，任一角色提升概率 16.9%。武器被动为固定精炼近似值，因此具体武器排名不应视为完整 Pando 结论。报告见 `examples/simulated-account/weapon-report.md`。这一步验证了“武器唯一 + 圣遗物唯一”的二维分配可以跑通，后续可把真实武器被动表接入同一接口。

### 自动刷取与配装更新

`tools/farming_loop.py` 把“推荐秘境 → 模拟掉落 → 强化 → 重跑武器+圣遗物联合优化”串成循环，用来观察继续刷取时配装和副本推荐如何变化。它先基于当前配装排一次副本收益，每轮从推荐秘境（或 `--fixed-domain`）模拟若干件五星掉落，强化后写入库存副本，再重新做二维联合优化。

```powershell
python tools/farming_loop.py
python tools/farming_loop.py --rounds 5 --drops-per-round 10
python tools/farming_loop.py --fixed-domain 982 --rounds 2 --drops-per-round 6
```

默认 3 轮、每轮 8 件五星、候选秘境 `1151,361,982,221`，并把 `account.sqlite3` 复制到输出目录后再写入，避免污染原始模拟账号。输出目录 `examples/simulated-account/farming-loop` 包含：

- `farming-account.sqlite3`：加入模拟掉落后的库存副本；
- `farming-report.json`：每轮掉落、配装、武器分配、副本排名和最终结果；
- `farming-report.md`：人类可读的刷取过程、最终配装和下一步推荐。

Python 入口是 `simulate_farming_loop(inventory, members, rounds=3, drops_per_round=8, ...)`；底层新增 `rank_domains_for_loadouts`，可以在不重跑联合搜索的情况下，对固定配装排副本收益。常用参数：

- `fixed_domain`：固定刷某个秘境，不跟随推荐；
- `enhance_to`：模拟掉落强化等级，默认 20；设为 0 可模拟不强化；
- `target_gain_pct`：队伍伤害相对当前配装达到该增幅后提前停止；
- `equip_best`：把每轮最优配装写回库存副本的角色档案，默认只写掉落；
- `max_nodes`、`restarts`、`max_iterations` 控制联合搜索预算。

每件五星按一次 20 树脂结算，只模拟五星；默认把所有掉落强化到 +20，真实账号通常只强化有潜力的胚子。武器被动仍是固定精炼近似值。精确单件替换枚举加上联合搜索会使完整多轮实验需要数分钟，候选秘境或角色越多越慢。

当前示例（3 轮 × 8 件五星，`--max-nodes 30000`）：基线 **52,875**，第 1 轮推荐 **芬德尼尔之顶**，8 件掉落中 2 件进入神里绫华的最优配装，队伍伤害升到 **56,909（+7.63%）**；第 2、3 轮推荐转为 **罪祸的终末**，新增 16 件掉落均未进入最优配装，伤害保持不变。最终阿蕾奇诺 +7.59%、雷电将军 +4.48%、芙宁娜 +4.75%、神里绫华 +11.20%，下一步仍推荐罪祸的终末。第 1 轮的大部分提升来自武器+圣遗物联合优化本身，两件冰风迷途的勇士把绫华从 8,536 提升到 9,041，也展示了“刷取掉落 → 更新推荐”的闭环。完整报告见 `examples/simulated-account/farming-loop/farming-report.md`。

### 库存和配装

默认数据库为当前目录下的 `artifacts.sqlite3`。可以用 `inventory --db <路径>` 操作另一份库。

```powershell
$created = python -m artifact_sim inventory add --set 15048 --slot flower --lines 3 --seed 73 | ConvertFrom-Json
$id = $created.ids[0]
python -m artifact_sim inventory list --set 15048 --slot flower --sort lvdesc
python -m artifact_sim inventory enhance $id
python -m artifact_sim inventory equip 阿贝多 $id --preview
python -m artifact_sim inventory equip 阿贝多 $id
python -m artifact_sim inventory panel 阿贝多
python -m artifact_sim inventory preferences --score crit --score er --high-score 35
python -m artifact_sim -o backup.json inventory export
python -m artifact_sim inventory --db recovered.sqlite3 restore backup.json --replace
```

用 `inventory --help` 查看上锁、标为已看、计数、重置、删除与撤销、卸下、配装改名等操作。装备预演返回目标配装的前后属性。同部位从另一角色换入时，对方接收目标角色该部位原装备。

单件删除支持撤销。批量清空保留上锁及已装备的物品，删除的物品不进入撤销记录。导出备份包含库存、配装、设置和可撤销的删除记录。恢复时默认合并，标识或装备位置发生冲突则回滚，`--replace` 用于整库替换。旧版导出可以用 `inventory import-legacy <文件>` 导入。已记录的词条按档位映射到当前数值表，对于旧版未记录的第 4 条，按物品标识生成一次并保存。

### 离线游戏图鉴

7.2 快照有 **35 类、9,382 条记录**。其中包含 126 条角色、256 把武器、129 组天赋、129 组命座、63 套圣遗物、284 条秘境关卡、348 条敌人和 927 种材料。还可查料理和合成、观景点、成就、名片、时装、野生动物及七圣召唤。角色档案关联天赋参数、命座和养成消耗，武器详情含副属性、精炼 R1–R5 和突破材料。284 条秘境关卡包括多个难度和多种秘境类型，其中五星圣遗物模拟沿用 22 个对应的入口。

```powershell
python -m artifact_sim data info
python -m artifact_sim data list weapons --version 7.2 --where rarity=5
python -m artifact_sim data character 米提亚
python -m artifact_sim data stats characters 瓦列里 --level 80 --ascension after
python -m artifact_sim data stats weapons 秘星典谕 --level 90
python -m artifact_sim data show materials 113093
python -m artifact_sim data show enemies 浪涛锚定者
python -m artifact_sim data list tcgcharactercards --version 7.2
```

Python 入口为 `game_data_info`、`list_game_records`、`get_game_record`、`get_character_profile` 和 `get_game_stats`。详情查询接受中英文名称、游戏 ID 或图鉴键。同名详情需要用 ID 或图鉴键区分。等级属性保留原始单位，百分比副属性中的 0.882048 表示 88.2048%。敌人等级属性是基础面板，浪涛锚定者采用可追溯的 `monster_id=25134601` 变体。环境、活动难度和战斗状态另有修正。

图鉴按类别和语言压缩打包，查询时按需加载。角色、武器等主体资料支持简体中文和英语，元素、星级等 4 个类别的上游只有英语。缺失字段沿用上游缺省，`--version` 只筛选有首次收录版本标签的条目。`areas` 目前含 7.2 公告新增的两个区域，`geographies` 是上游观景点图鉴。补录的 6 项养成材料有名称、ID 和能从提取表核对的字段，首次出现版本留空。

## Python 调用

```python
from artifact_sim import Artifact, Simulator, loadout, score_weights

sim = Simulator(seed=73)
base = sim.drop("逆悬的冰河", four_line_probability=0)
assert base.preview is not None
at_four = sim.enhance(base, 4)
assert at_four.substats()[base.preview.stat] == base.preview.value
at_twenty = sim.enhance(at_four, 20)
saved = at_twenty.to_state()
assert Artifact.from_state(saved) == at_twenty
crit_only = at_twenty.score(score_weights("crit"))
panel = loadout([at_twenty])
```

多角色联合配装：

```python
from artifact_sim import PartyMember, get_damage_model, optimize_party, plan_party

members = [
    PartyMember(
        model=get_damage_model("那维莱特"),
        character={"id": "Neuvillette", "level": 90, "ascension": 6},
        weapon={"id": "万世流涌大典", "level": 90, "ascension": 6},
        target_four_piece_set=15031,
    ),
    PartyMember(
        model=get_damage_model("阿蕾奇诺"),
        character={"id": "Arlecchino", "level": 90, "ascension": 6},
        weapon={"id": "赤月之形", "level": 90, "ascension": 6},
        target_four_piece_set=15035,
    ),
]
joint = optimize_party(pieces, members)
report = plan_party(pieces, members, threshold_pct=1.0)
```

## 数据与范围

- 游戏快照为 7.2，包含 63 套圣遗物，其中 46 套有五星，22 个秘境有标准五星掉落，并提供 126 条角色数据。 [游戏 7.2 公告](https://genshin.hoyoverse.com/en/news/detail/167187) · [genshin-db 5.2.15](https://github.com/theBowja/genshin-db/tree/73d57660053377e55beb12e8f627aabd7ef4cc0d)
- 主词条逐级数值、副词条档位取自 [游戏数据提取表](https://gitlab.com/Dimbreath/AnimeGameData/-/tree/433284d22ef1a704e9d49ae22fb96cf7a245828e/ExcelBinOutput) 的 `ReliquaryLevelExcelConfigData.json` 和 `ReliquaryAffixExcelConfigData.json`。原始数值的精度由提取表决定。
- 概率取自本机 GenshinAnalyzer 的 [五星秘境 PMF](D:/Projects/GenshinAnalyzer/genshin_artifact_pmf_package/genshin_artifact_5star_domain_pmf.json)。秘境两套各 50%，五部位各 20%，初始三条与四条默认 80% / 20%，其余权重见 `data/data.json`。这是概率模型，初始四条概率可配置。
- [月之一官方公告](https://genshin.hoyoverse.com/en/news/detail/159349)确认了五星三词条件的第 4 条预览。本实现保存预览的属性、档位，并使用同一数值激活。
- 圣遗物模拟覆盖五星秘境单件掉落及常规强化。实际树脂或强化材料消耗、还圣奥迹、圣遗物定轨与重塑不在模拟范围。
- 角色模型目录由 `tools/calibrate_character_models.py` 生成，使用固定提交的 Genshin Optimizer 公式与倍率，并为伤害天赋 1–10 级保存相对倍率。评分和 UI 共享结构化因子，按外部面板、天赋等级与所选套装生成表达式；没有模型定义的武器被动、套装效果、元素反应和队伍增益仍不计入。标定误差、假设和来源随每个模型保存在 `damage/character_models.json`。
- 多角色联合配装在无圣遗物争用时精确；存在争用时使用贪心、坐标上升和同部位交换的启发式搜索。多角色刷取推荐复用精确的单件替换分布，但“多角色期望增幅之和”是上界，保守值见 `best_member_expected_gain_pct`。元素反应、队伍增益和全局多件联换仍未纳入。

更新数据时，从上述来源取得新版本的套装、秘境和数值表，检查覆盖范围后运行下面的生成命令。脚本记录输入表的 SHA-256。更新测试中的版本与已核实的数量后运行回归测试。

```powershell
node tools/build_snapshot.cjs <genshin-db包目录> <五星秘境PMF的JSON路径> <包含两张ExcelBinOutput表的目录> <已核实游戏版本>
```

图鉴可独立生成。使用 genshin-db 5.2.15、TCG 发布提交 `db37a96e1dac3f56d0e6f015c4ff4f87c25577cd` 的 `data/gzips/` 中英文七圣召唤分表，以及 7.2 提取提交 `d54f6f0d423ded9d055bb740a3e5f3aa8f2667f1` 的中英文 TextMap 和 `MonsterDescribe`、`Monster`、`MonsterCurve`、`Material` 四张 `ExcelConfigData.json` 表。图鉴 [manifest.json](data/reference_data/manifest.json) 记录输入和输出哈希及各分类数量。 [七圣召唤发布数据](https://github.com/theBowja/genshin-db-dist/tree/db37a96e1dac3f56d0e6f015c4ff4f87c25577cd/data/gzips) · [7.2 游戏提取表](https://gitlab.com/Dimbreath/animegamedata2/-/tree/d54f6f0d423ded9d055bb740a3e5f3aa8f2667f1) · [7.2 官方更新说明](https://ys.mihoyo.com/main/news/detail/167205)

```powershell
node tools/build_reference_data.cjs <genshin-db包目录> <TCG压缩数据目录> <TCG提交> <TextMap和Excel表目录> <7.2提取提交>
python -m unittest discover -s tests -v
```
