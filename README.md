# naval_command
一个把钢铁雄心四战略地图上的海军交战交给外置回合制战术海战的桥接 mod。钢铁雄心四里的海战不再只靠骰子结算；你可以在naval_command的六角格海图上亲自指挥。
# 海军战术指挥 / Naval Tactical Command

> **便携版提示：** 本文部分路径以原开发目录为例。移植到其他电脑后，请先看 **`README-便携版.md`**，双击 **`设置路径.bat`** 编辑 `settings.json`。下面的 ASCII junction 是旧开发机配置，便携版不再依赖它。

一个把 **HOI4 战略地图上的海军交战** 交给 **navy-out 战术海战** 的桥接 mod。

- HOI4 里的舰队不再只靠骰子结算；你可以在 navy-out 的六角格海图上亲自指挥。
- 进入战斗前，桥接程序从存档里读出**真实舰队**：每艘船的舰名、舰种、当前结构值、组织度、装备模块、升级、科技与海军学说，以及交战海域的地形（浅海 / 深海 / 峡湾）。
- 战斗结束后，沉没舰船、结构值损失和组织度损失写回 HOI4 的真实舰队。

---

## 1. 安装位置
navy\
├─ naval-tactical-command\        HOI4 mod 源码（决议、事件、本地化）
├─ bridge\                       Python 桥接程序
│  ├─ navy_command_bridge.py     主程序
│  ├─ designer.py                舰船/飞机设计器逐项计算
│  ├─ run_bridge.bat             双击启动桥接程序
│  ├─ launch_game.py             强制启用本地 mod 并直接启动 HOI4
│  ├─ install.bat / install.ps1  安装 mod 到 HOI4 用户目录
│  ├─ setup_ascii_userdir.ps1    创建 ASCII 用户目录 junction
│  ├─ config.json                路径与行为配置
│  └─ selftest.py                离线自检
├─ settings.json                 玩家路径设置（移植后优先改这里）
├─ 设置路径.bat                  打开 settings.json 并检查路径
├─ 安装MOD.bat                   按 settings.json 安装 mod
├─ 启动桥接程序.bat              启动桥接程序
├─ README-便携版.md              便携版首次使用说明
├─ README-海军战术指挥.md        本文件
├─ NOTICE.md                     来源与许可说明
└─ LICENSE                       GPL-3.0

## 2. 启用 mod

1. 打开 HOI4 启动器。
2. 在播放集里启用 **Naval Tactical Command (Local) / 海军战术指挥（本地）**。
3. 启动游戏。


## 3. 每局怎么玩

### 第一步：启动桥接程序

双击：

D:\game\navy\bridge\run_bridge.bat

应看到 `Using Python: ...`、程序 banner，最后是：

text
Waiting for HOI4 signals (click Scan Naval Battles). Ctrl+C to exit.

**保持窗口开着**；关闭它等于停止桥接程序。

### 第二步：在 HOI4 里等待海上交战

你的舰队必须**出海**，和敌国舰队处在**同一片海域**，并且两国处于战争状态。

### 第三步：暂停游戏，点击决议

在决议面板找到 **海军战术指挥** 分类，点击：

> **战术指挥：扫描海上交战**

桥接程序会：

1. `gamespeed 0` 暂停游戏
2. 让 HOI4 写一份新快照
3. 解析舰队、舰载机、海区地形和天气
4. 弹出游戏内事件，最多列出 3 场交战

### 第四步：选择一场交战

事件会显示海区、地形、我方/敌方舰船数。选择后游戏保持暂停。

### 第五步：在 navy-out 里打

navy-out 会带着真实舰队数据启动：

- 每艘船的真实舰名、舰种、当前结构值/组织度
- **逐项设计器计算**出的轻/重攻击、穿深、鱼雷、装甲、防空、航速、可靠性
- **真实航母舰载机中队**，一个中队 = 一架对应飞机
- HOI4 海区对应的地图预设与开局天气

### 第六步：战果自动写回

navy-out 判定胜负后，桥接程序：

1. 解析每艘船的剩余 HP/组织度、沉没与撤离名单
2. 用控制台效果写回：
   - 沉没舰船：按舰种和数量移除
   - 幸存舰船：按该场战斗的总损失比例扣组织度和结构值
3. 弹出“战术指挥：战斗结束”事件

关闭 navy-out，回到战略地图继续游戏。

---

## 4. 数据通道

| 通道 | 方向 | 内容 |
|---|---|---|
| `game.log` | mod → 桥接程序 | `ntc_scan` / `ntc_pick` / `ntc_abort` 等 JSON 行 |
| 存档文件 | HOI4 → 桥接程序 | 舰队、舰船、舰载机中队、组织度、结构值、装备变体、科技、学说、海区 |
| 控制台注入 | 桥接程序 → HOI4 | `savegame`、`event`、`set_variable`、`damage_units`、`destroy_ships` |

桥接程序不联网，只读取 HOI4 用户目录、HOI4 安装目录和 `D:\game\navy` 下的文件。

---

## 5. 回写规则与已知限制

### 5.1 沉没舰船

按**舰种 + 数量**回写：

```hoi4
eval_effect ENG = { destroy_ships = { type = submarine count = 5 } }
```

HOI4 脚本没有“按舰名击沉某艘船”的效果，因此实际被移除的可能是同型号的另一艘船。**总数量与舰种准确**，具体哪一艘由 HOI4 决定。

结果标题匹配做了三层兜底：精确标题 → 按舰种匹配 → 初始数量与结果剩余数量的差额。即使 navy-out 的标题格式变化、舰名带后缀或改名，也不会再把整场损失静默成 0。

`destroy_ships` 的 `type` 使用 **HOI4 船体原型**，不是存档里的 `definition`：

| 存档 definition | destroy_ships type |
|---|---|
| `destroyer` | `ship_hull_light` |
| `light_cruiser` / `heavy_cruiser` | `ship_hull_cruiser` |
| `battleship` / `battle_cruiser` | `ship_hull_heavy` |
| `carrier` | `ship_hull_carrier` |
| `submarine` | `ship_hull_submarine` |

因此轻/重巡共用 `ship_hull_cruiser`、战列/战巡共用 `ship_hull_heavy`；**总数量准确，但同一船体家族内具体移除哪一级由 HOI4 决定**。

### 5.2 幸存舰船的组织度 / 结构值

HOI4 没有“设置单艘船 HP”的脚本效果。桥接程序使用：

```hoi4
eval_effect damage_units = { province = <海区> limit = { tag = <国家> } org_damage = 0.12 str_damage = 0.34 ratio = yes navy = yes }
```

这是**整支在场舰队的聚合伤害**：

- 总结构值损失、总组织度损失、沉没数量准确
- 单艘幸存船之间的伤害分布是均匀的，无法做到“某艘船掉 70%、另一艘没掉”
- 这是 HOI4 脚本能力的硬限制
- 如果一侧全灭，额外对该海区该国家施加 100% 组织度/结构值伤害；即使按舰种击沉移除了别的同型船，原版海战也会因为场上舰船组织度归零而结束
- 损失/伤害命令与结果弹窗分两批注入；即使弹窗的本地化字符串出问题，损失仍然已经写回

### 5.3 撤离舰船与全员脱离

结果文件只记录“已撤离”名单，不记录撤离时的 HP/组织度。桥接程序把撤离舰船视为**保持当前状态**，不追加伤害。

如果**任意一方或双方的所有舰船都是主动撤离**（该方没有沉没、没有剩余），桥接程序不会执行 `destroy_ships`，也不会扣任何结构值。但 HOI4 没有“无伤强制结束海战”的脚本效果，不处理的话原版海战会继续。由 `retreat_action` 选择：

| 值 | 行为 |
|---|---|
| `"org"`（默认） | 只对**主动撤离的那一方**在该海区的舰队施加 100% **组织度**伤害，不扣结构值；组织度归零后该方脱离接触，海战结束，随后自然恢复。仍留在战场的一方组织度不变，但它在小规模海战中的结构值/组织度战损仍按结果写回。 |
| `"none"` | 完全不施加任何组织度/结构值伤害，只结束战术指挥并弹出结果事件。原版海战可能继续，需要玩家手动让舰队撤离（选中舰队 → 撤离/返回港口）。 |

`"org"` 换来的是一次性组织度清空，目的是让引擎判定撤离方脱离接触；它不是永久损失，组织度会按正常速度恢复。撤离方本身不会损失舰船或 HP。如果你要求连组织度也完全不变，请把 `retreat_action` 设为 `"none"`，并接受手动撤离。

### 5.4 航母航空兵与水上飞机侦察

HOI4 舰载机是独立中队。桥接程序解析：

- `air_base={ carrier={ id=<舰船ID> } }` → 哪个机库属于哪艘航母
- `air_wing_pool` → `air_wings` → 中队类型、飞机数量、装备型号

规则：

- **一个中队 = 一架对应飞机**
- `cv_fighter` / `cv_interceptor` → 战斗机
- `cv_nav_bomber` / `cv_cas` → 海军轰炸机
- 同一艘航母的每个中队都生成一架战术飞机；`count` 会读取并记录，但不再按飞机数生成多架

**额外的海军侦察机**：

- 每艘装备 `ship_airplane_launcher_*`（水上飞机弹射器）**或** `ship_seaplane_tender_*`（水上飞机母舰）模块的舰船 → **+1 架海军侦察机**
  - 同一艘船同时拥有两种模块时仍然只加 1 架
- 每艘航母 → **+3 架海军侦察机**（在这艘航母的真实舰载机中队之外）
  - 如果航母本身也带弹射器/水上飞机母舰模块，则再额外 +1（3 + 1）
- 侦察机使用 navy-out `naval_recon` 的默认属性（对海攻击 0、空战能力 2、机动 60、航速 200、视野 4）
- 侦察机损失不写回 HOI4；它们只影响战术海战中的侦察/视野
- 这两条侦察机规则独立于 `carrier_air_wings`：即使关闭真实舰载机中队，航母的 3 架侦察机和弹射器/母舰的 1 架侦察机仍会生成

飞机属性来自 HOI4 飞机设计器：

- 对海攻击 = `naval_strike_attack`
- **空战能力 = 对空攻击 + 对空防御 + 机动**
- 机动 = `air_agility`
- 速度 = `maximum_speed`
- 可靠性 = `reliability`
- 鱼雷/炸弹模块的 `mission_type_stats`（例如 `torpedo_mounting` 的 `naval_strike_attack = 12`）按舰载机任务类型计入

HOI4 飞机设计器没有单独的 HP/组织度字段，这两项使用 navy-out 机型默认值；`naval_aim` 是 navy-out 特有系数（战斗机 1.35、海军轰炸机 4.0）。

舰载机损失不写回 HOI4（它们不是舰船）；航母自身的 HP/组织度正常写回。

### 5.5 舰船属性：逐项复刻设计器公式

舰船属性不使用 navy-out 的舰种基线，也不使用“模块等级百分比近似”。计算流程：

1. **船体基础值**：`common/units/equipment/ship_hull_*.txt`，沿 `archetype` / `parent` 链继承
2. **模块**：`00_ship_modules.txt`
   - `add_stats`：平铺相加
   - `multiply_stats`：加法百分比（`0.05` = +5%）
   - `add_average_stats`：穿甲深度取所有相关武器模块的**平均值**；其他平均属性按设计器规则
3. **衍生型升级**：存档里的 `upgrades={ upgrades={ { ship_gun_upgrade 2} ... } }`，按每级百分比累加；`carrier_size` 是平铺值
4. **设计团队加成**：`design_team_bonus`
5. **最终值**：

```text
(base + add_stats + average_stats) × (1 + Σ multiply_stats + Σ upgrade% + design_team_bonus)
```

**注释处理**：引擎模块里有大量被注释掉的旧值，例如：

```text
add_stats = {
    #naval_speed = 10
}
```

这些注释不会被计入。之前所有船航速偏高的原因就是把 `#naval_speed = 10` 当成了真实属性；现在统一在解析前剥离注释。

**穿甲深度**：`add_average_stats` 的轻/重穿甲取所有相关模块的平均值，不能按“最高值 + 其余平均值”计算。之前两门同型主炮会让穿甲深度翻倍，现已修正为平均值。

映射到 navy-out：

| HOI4 设计器属性 | navy-out 属性 |
|---|---|
| `lg_attack` / `lg_armor_piercing` | `light_atk` / `light_pen` |
| `hg_attack` / `hg_armor_piercing` | `heavy_atk` / `heavy_pen` |
| `torpedo_attack` | `torpedo` |
| `sub_attack` | `depth_charge` |
| `armor_value` | `armor` |
| `anti_air_attack` | `aa` |
| `naval_speed` | `speed` |
| `reliability` | `reliability`（0–1 ×100） |
| `max_strength` | `max_hp` |
| `surface_visibility` / `sub_visibility` | `surface_vis` / `sub_vis` |
| `surface_detection` / `sub_detection` | `sea_detect` / `sub_detect` |

当前/最大组织度直接取自存档（已含科技/学说影响）。桥接程序不额外叠加近似的科技/学说百分比，避免重复计算。

示例（真实存档计算值）：战列舰 22.86 节、重巡 26.22 节、轻巡 33.3 节、驱逐舰 33.92 节、航母 29.62 节、潜艇 13.52 节。

### 5.6 其他

- 只把**玩家本国**的舰船算作“我方”；盟军舰队不并入
- 每场候选交战对应**一个敌国**；同海区多个敌国会拆成多场候选
- 只统计已编入任务部队、且位于海区的舰船；港内和预备舰队不参战
- 游戏必须在桥接程序注入时保持运行且窗口未最小化

---

## 6. 配置

`bridge\config.json`：

```json
{
  "hoi4_user_dir": "",
  "hoi4_game_dir": "",
  "navy_out_exe": "",
  "navy_out_workdir": "",
  "session_dir": "",
  "cache_dir": "",
  "selection": "event",
  "max_battles": 3,
  "carrier_air_wings": true,
  "retreat_action": "org",
  "inject_dry_run": false,
  "log_from_start": false,
  "save_name": "navy_command_scan"
}
```

| 字段 | 说明 |
|---|---|
| `hoi4_user_dir` | 留空自动检测；现在优先使用 ASCII junction |
| `hoi4_game_dir` | 留空自动检测，指向含 `hoi4.exe` 的目录 |
| `navy_out_exe` | 留空自动检测为 `..\navy-out\navy.exe` |
| `selection` | `event` = 游戏内事件选择；`dialog` = 桌面窗口选择 |
| `max_battles` | 最多列出几场，1–3 |
| `carrier_air_wings` | 是否把真实舰载机中队加入战术海战（一个中队 = 一架对应飞机） |
| `retreat_action` | 任意一方全员主动撤离时：`org` = 仅清空撤离方组织度以结束海战（撤离方 HP 无损，组织度恢复）；`none` = 完全无伤害，但可能需要手动撤离 |
| `inject_dry_run` | 只写控制台批处理文件，不真的发按键 |
| `log_from_start` | 启动时从 `game.log` 开头读，用于补收已点击的扫描 |

如果游戏内事件里的战况文字显示不正常，把 `selection` 改成 `"dialog"`，重启桥接程序。

---

## 7. 故障排查

### 决议没有出现

- 检查 `system.log` 的 `Active Mod Count` 是否为 1
- 检查是否只启用了本地条目，没有同时启用同名创意工坊副本
- 检查 `.mod` 的 `path=` 是否指向 ASCII junction 内的 `mod/naval-tactical-command`
- 检查 `setup.log` 是否加载了 `ntc_decisions.txt` / `ntc_categories.txt`
- 检查 `error.log` 是否有 `ntc` 相关错误
- 如果还不行，在游戏控制台执行：

```text
eval_effect ITA = { if = { limit = { has_decision = ntc_scan } log = "NTC_HAS_DECISION" } else = { log = "NTC_NO_DECISION" } }
```

然后看 `logs\game.log`：`NTC_HAS_DECISION` 说明决议已注册；`NTC_NO_DECISION` 说明文件仍未加载。

### 点击扫描后没有反应

- 桥接程序没有启动，或启动在错误的 HOI4 用户目录
- 游戏窗口被最小化
- 杀毒软件拦截模拟按键；把 `python.exe` 和 `D:\game\navy\bridge` 加入白名单
- 12 个游戏小时后游戏内会提示“外部程序没有回应”并解除扫描锁定

### 没有可接管的交战

- 舰队还在港口，没有出海
- 没有和敌国处于战争状态
- 双方舰队不在同一片海区
- 海战已经结束，舰队分散

### navy-out 黑屏 / 打不开

- navy-out 自身的图形/D3D12 问题；先单独运行 `navy-out\navy.exe`
- 尝试窗口化、更新显卡驱动，或用 Godot 4.7 直接运行 `D:\game\navy\navy`
- 黑屏不会破坏 HOI4 存档

### 回写失败

- 桥接程序控制台会打印原因
- 用游戏内 **“战术指挥：放弃 / 取消”** 清除状态
- 检查游戏窗口是否最小化、控制台是否被占用

### 桥接程序崩溃

- 重新运行 `run_bridge.bat`
- 用游戏内 **“战术指挥：放弃 / 取消”** 清除标志
- 重要存档请先备份

---

## 10. 兼容性与免责声明

- 目标版本：HOI4 **1.19.\***（Operation Postern）
- 不支持多人、铁人、二进制存档
- 通过 Windows 模拟按键在 HOI4 控制台执行命令；请自行备份存档
- HOI4 及其素材归 Paradox Interactive AB 所有；navy-out 归其作者所有
- 本 mod/桥接程序是非官方爱好者作品，与 Paradox、navy-out 作者无隶属或背书关系

---

## 11. 致谢

- **Forward Command**：控制台注入、`game.log` 信号、存档解析架构参考
- **navy-out**：外部开局/结果 API
- HOI4 原版决议/事件/本地化格式与图标素材

详细来源与许可见 `NOTICE.md`。
