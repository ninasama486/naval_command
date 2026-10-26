# navy-command bridge — 技术说明

## 模块

| 文件 | 职责 |
|---|---|
| `navy_command_bridge.py` | 主状态机：监听日志、扫描、选择、启动 navy-out、回写 |
| `config.py` | 读取项目根 `settings.json`、自动检测路径与配置校验 |
| `check_setup.py` | 检查 settings.json 与所有必要文件，输出缺失项 |
| `log_tail.py` | 增量读取 `game.log`，提取 `{"type":"ntc_..."}` 行 |
| `hoi4_save.py` | 定向解析 HOI4 文本存档：舰队、任务部队、舰船、舰载机中队、战争、科技、学说、天气、装备变体 |
| `ship_names.py` | 从 `name_order` + `common/units/names_ships/*.txt` 还原真实舰名 |
| `hoi4_map.py` | `map/definition.csv` + `map/strategicregions/*.txt` → 海区、地形、navy-out 预设 |
| `designer.py` | 逐项复刻 HOI4 舰船/飞机设计器：船体 + 模块 + 升级 + 设计团队 |
| `ship_stats.py` | HOI4 舰船/舰载机 → navy-out 单位字段；航母中队映射 |
| `battle.py` | 候选海战发现、开局 JSON、结果 → 回写计划 |
| `result_parser.py` | 解析 navy-out 的 `battle_result.txt` |
| `hoi4_console.py` | Win32 控制台注入（PostMessage + SendInput 前台回退） |
| `hoi4_commands.py` | 生成 HOI4 控制台命令（变量、事件、伤害、沉船） |
| `navy_out.py` | 启动 navy-out、轮询结果文件 |
| `launch_game.py` | 真实用户目录 + ASCII mod 源路径的启动/修复 |
| `selftest.py` | 离线自检（64 项） |

## 玩家设置

普通玩家只编辑项目根目录的 `settings.json`；它覆盖 `bridge/config.json` 的同名字段。

- 相对路径按项目根目录解析，整个项目移动后仍可用。
- 留空字段自动检测。
- `mod_source_dir` 是 HOI4 实际挂载的 mod 源目录，推荐纯 ASCII。
- `python check_setup.py` 或根目录 `设置路径.bat` 会检查并列出缺失路径。
- `install.ps1` 和 `launch_game.py` 都读取 `settings.json`。

`bridge/config.json` 只保留高级默认值，玩家通常不需要改。

## 状态机

```
idle
  │  ntc_scan
  ▼
scanning ── 无候选 ──► ntc_quiet.1 ──► idle
  │ 有候选
  ▼
awaiting_pick ── ntc_cancel / 空槽 ──► idle
  │ ntc_pick
  ▼
in_battle ── ntc_abort（放弃决议）──► 终止 navy-out ──► idle
  │ 结果文件完整
  ▼
写回命令 + ntc_end.1 ──► idle
```

游戏侧状态标志：

- `ntc_scan_waiting`：扫描请求已发出，等待桥接程序回应
- `ntc_mode_active`：玩家已接管一场战斗

所有退出路径都会清除两个标志并重置决议冷却。

## 协议（game.log）

```json
{"type":"ntc_scan","tag":"ITA","ver":"0.1.0"}
{"type":"ntc_scan_open"}
{"type":"ntc_pick","slot":2,"tag":"ITA"}
{"type":"ntc_cancel","tag":"ITA"}
{"type":"ntc_abort"}
{"type":"ntc_complete"}
{"type":"ntc_quiet"}
{"type":"ntc_timeout"}
```

`log_tail.py` 容忍行首的游戏日期前缀，并支持跨读取的半行。

## 存档解析假设（1.19.x）

- 舰队块：`fleet={ ... task_force={ ... ship={ ... } } }`
- 舰船字段：`id={ id=N type=51 }`、`definition=...`、`organisation`、`strength`、`equipment`、`ship_name`
- **`strength` 是绝对结构值**，不是百分比；当前值直接作为 navy-out `hp`
- 最大结构值由设计器公式计算；当前/最大组织度取存档值
- 战争关系：`war_relation={ first="A" second="B" ... }`
- 海军学说：`doctrine={ countries={ { ... grand_doctrine="..." folder="naval" ... country="TAG" } } }`
- 科技：国家块内 `technology={ technologies={ ... } }`
- 天气：`weather={ provinces={ "P"={ water=... snow=... } } }`
- 舰名组：`common/units/names_ships/*.txt`
- 舰载机：`air_base={ carrier={ id=... } }` + `air_wing_pool` → `air_wings`

解析器不做完整 Clausewitz AST，避免 70 MB 存档膨胀成数百 MB 对象；只对需要的块做括号匹配。

## 设计器计算（逐项复刻）

`designer.py` 读取：

- `common/units/equipment/ship_hull_*.txt`（沿 `archetype`/`parent` 继承基础值）
- `common/units/equipment/modules/00_ship_modules.txt`
- `common/units/equipment/upgrades/naval_upgrades.txt`
- `common/units/equipment/plane_airframes.txt`、`*_engine_airframe.txt`、`x_plane_airframes.txt`
- `common/units/equipment/modules/00_plane_modules.txt`
- `common/units/equipment/upgrades/air_upgrades.txt`

聚合规则：

- `add_stats`：平铺相加
- `multiply_stats`：加法百分比（0.05 = +5%）
- `add_average_stats`：轻/重穿甲取所有相关模块的平均值；其他平均属性按设计器规则
- `mission_type_stats`：按舰载机任务类型（`naval_bomber` / `air_superiority` 等）计入
- 衍生型 `upgrades`：每级百分比累加；`carrier_size` 平铺
- `design_team_bonus`：百分比累加
- 最终：`(base + add + average) × (1 + Σmultiply + Σupgrade + design_bonus)`

**注释剥离**：所有脚本在解析前先去掉 `#` 注释。引擎模块里存在 `#naval_speed = 10` 这类旧值；之前把注释当属性导致全船航速偏高 5–10 节，现已修复。

**穿甲深度**：`add_average_stats` 的轻/重穿甲取所有相关武器模块的平均值。之前按“最高值 + 其余平均值”计算，两门同型炮会让穿甲深度翻倍；现已修正为平均值。

舰船字段映射见 `designer.to_navy_ship()`；飞机映射见 `to_navy_plane()`。
飞机“空战能力”是 `air_attack + air_defence + air_agility`。

真实存档校验示例：

- 战列舰 22.86 节、重巡 26.22 节、轻巡 33.3 节、驱逐舰 33.92 节、航母 29.62 节、潜艇 13.52 节
- 海军轰炸机 `naval_atk=13.65`、`air_atk=25`（对空攻击 0 + 对空防御 7 + 机动 18）

## 航母舰载机与水上飞机侦察

- 解析 `air_base={ carrier={ id=N type=51 } }` 建立“机库 → 航母”映射
- 解析 `air_wing_pool` → `air_wings`，读取 `definition`、`count`、`equipment`
- 每个中队生成一架 navy-out 飞机：`cv_fighter`→fighter，`cv_nav_bomber`/`cv_cas`→naval_bomber
- 中队 `count` 只表示编制规模；按需求一个中队对应一架战术飞机
- **每艘航母额外 +3 架 `naval_recon`**（在真实舰载机中队之外）
- **每艘带 `ship_airplane_launcher_*` 或 `ship_seaplane_tender_*` 模块的舰船 +1 架 `naval_recon`**
  - 同舰两种模块都有只加 1；航母若也带这些模块则 3+1
- 侦察机使用 navy-out `naval_recon` 默认属性；损失不写回 HOI4
- 飞机损失不写回 HOI4；航母自身的 HP/组织度写回

## 控制台注入

1. 把命令写入 `HOI4用户目录\navy_command_inject.txt`（UTF-8，末尾必须有换行）
2. 按窗口标题前缀 `Hearts of Iron IV` 找窗口
3. PostMessage `~` 打开控制台，`WM_CHAR` 输入 `run navy_command_inject.txt`，Enter
4. 批处理第一行是探针 `eval_effect log = "NC_PING_..."`；在 `game.log` 尾部看到标记才算成功
5. 失败时 Escape 关闭控制台并重试一次
6. 仍失败则 `SetForegroundWindow` + `SendInput` 前台键入

`run` 只解析用户目录相对路径，所以批处理必须放在用户目录根部。

## 结果 → HOI4

- 沉没：`destroy_ships = { type = <船体原型> count = N }`
  - `destroyer`→`ship_hull_light`；轻/重巡→`ship_hull_cruiser`；战列/战巡→`ship_hull_heavy`；航母→`ship_hull_carrier`；潜艇→`ship_hull_submarine`
  - 传入 `definition`（如 `light_cruiser`）会被引擎忽略，**不会移除任何船**
- 幸存伤害：
  `damage_units = { province = <海区> limit = { tag = <TAG> } org_damage = x str_damage = y ratio = yes navy = yes }`
- 伤害分母只统计幸存舰船的最大值；沉没舰船先移除，再对幸存者施加聚合伤害
- 撤离舰船保持当前状态
- **任意一方或双方全员主动撤离**：撤离方不沉船、不扣结构值；`retreat_action="org"`（默认）只把撤离方组织度清零以结束原版海战，之后自然恢复；仍留场一方组织度不变，但其战损按结果写回；`"none"` 完全不伤害，但原版海战可能需要手动撤离
- 舰载机结果不会误当成舰船写回
- 标题匹配：精确 → 舰种 → 初始/剩余数量差额；全灭的一侧额外施加 100% 伤害，保证原版海战终止
- 损失/伤害与结果弹窗分两批注入，弹窗失败不会阻止损失写回

## 测试

```bat
python selftest.py
```

覆盖玩家设置合并/相对路径、便携启动自动生成 `.mod`、地图、存档、候选发现、开局 JSON、结果解析、回写计划、单方/双方全员撤离、舰载机损失隔离、命令语法、舰名库、设计器数据加载、引擎注释忽略。

已在本机验证：

- 真实 73 MB 存档：143 舰队 / 151 任务部队 / 1356 艘已部署舰船；1200 艘船还原出真实舰名
- 设计器：80 船体 / 117 舰船模块 / 77 机体 / 95 飞机模块；精确 HP、攻击、穿深、装甲、防空、航速
- 航速修复：`heavy_ship_engine_2` 的 `#naval_speed=10` 被忽略；真实舰船速度与设计器口径一致
- 航母舰载机：真实存档读出 47 个中队；`HMS Glorious` 的 4 个中队生成 4 架战术飞机，另加 3 架侦察机
- 侦察机规则：真实候选 1 艘航母 + 8 艘弹射器舰船 → 11 架 `naval_recon`；敌方 7 艘 → 7 架；`ExternalApi` 解析 0 错误、0 警告
- navy-out `ExternalApi.load_plan()` 解析含精确属性和舰载机的 `external_setup.json`：0 错误、0 警告
- 用游戏自身 `ExternalApi.result_text()` 生成的文本验证 `result_parser.py`
- mod 在 HOI4 1.19.3 中加载：`system.log` 显示 `Active Mod: Naval Tactical Command (Local)`
- 远程桌面无前台输入，未能现场跑通控制台注入；正常桌面会话需要 HOI4 保持前台

真实存档诊断：

```bat
python navy_command_bridge.py --test-save "...\save.hoi4" --build-setup 1
```

`--build-setup` 会写出 `session\external_setup.json` 和 `session\context.json`，可直接拿 navy-out 的 `ExternalApi` 校验。

`--dry-run` 只写控制台批处理，不发按键，用于检查命令文本。

## 安全边界

- 不联网
- 只读取 HOI4 用户目录、HOI4 安装目录、`D:\game\navy`
- 只写 `bridge\session`、`bridge\cache`、HOI4 用户目录下的 `navy_command_inject.txt`
- 通过控制台修改游戏状态；请备份重要存档
