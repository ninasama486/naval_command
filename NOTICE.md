# 来源、致谢与许可

## 本项目

**海军战术指挥 / Naval Tactical Command** 是一个非官方爱好者项目，与 Paradox Interactive AB 及 navy-out 的作者无隶属或背书关系。

- HOI4 mod：决议、事件、脚本效果、本地化。
- 桥接程序：Python 3，Windows。
- 战术海战：复用 `D:\game\navy\navy-out`（navy-out）及其 `docs/EXTERNAL_API.md` 外部接口。

## 架构参考

本项目的三条数据通道（`game.log` 信号、存档解析、控制台 `SendInput` 注入）参考了：

- **Forward Command** / 前敌指挥（作者 physica，GPL-3.0）
  <https://github.com/physica-271828/forward-command>

Forward Command 的 `DESIGN.md`、`tactical-inject`、`tactical-sync` 等实现证明了以下做法在 HOI4 1.19 上可行：

- 用 `log` 效果把 JSON 行写入 `game.log`；
- 用 `run <批处理>` 通过控制台执行 `eval_effect` / `set_variable` / `event`；
- 用 `damage_units` 的 `ratio=yes` 把战术伤害写回战略地图；
- 用窗口标题前缀定位 HOI4，并用 `PostMessage` 发送按键。

本项目未逐行复制其 Rust 代码；HOI4 mod 文件、Python 桥接程序与文档为本项目新写。

## 战术海战

- **navy-out** 的外部 API（`scripts/core/external_api.gd`、`docs/EXTERNAL_API.md`）用于注入开局数据与取回结果文件。
- 舰种基础数值取自 navy-out 的 `UnitDatabase`，仅作为平衡基线；本项目不修改 navy-out 本身。

## HOI4 数据

- 从使用者本机 HOI4 安装读取的舰名组、海区地形、战略区域名称等，版权归 Paradox Interactive AB 所有。
- 本 mod 不打包任何 HOI4 原版美术资源。

## 许可

本项目（HOI4 mod、Python 桥接程序、文档）按 **GNU General Public License v3.0** 发布，与 Forward Command 的许可保持一致。

完整文本见仓库根目录的 `LICENSE`。

## 免责声明

本工具通过 Windows 模拟按键在 HOI4 控制台执行命令，会修改游戏状态。虽然所有清理路径都做了处理，仍建议：

- 使用前备份重要存档；
- 不要在铁人/多人游戏中使用；
- 不要在未确认结果的情况下直接覆盖唯一存档。

使用风险自担。
