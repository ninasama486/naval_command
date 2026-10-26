# 海军战术指挥（便携版）

这个文件夹提供桥接程序和 navy-out。HOI4 mod 本身需要单独安装，或者指向已经安装好的 mod 源目录。

## 第一次使用

1. 安装 Python 3.10 或更新版本。
2. 双击 **`设置路径.bat`**。
3. 在打开的 `settings.json` 中填写：

   - `hoi4_game_dir`：HOI4 安装目录，里面要有 `hoi4.exe`
   - `hoi4_user_dir`：HOI4 用户目录，里面要有 `save games` 和 `logs`
   - `mod_source_dir`：MOD 文件夹；如果 mod 已经安装，可以留空
   - `navy_out_exe` / `navy_out_workdir`：通常留空即可，默认使用本文件夹里的 `navy-out`

   路径可以用 `/`，相对路径按本文件夹解析。留空会自动检测。
4. 保存并关闭记事本，脚本会自动检查所有文件，并列出缺少的路径。
5. 如果提示没有安装 mod，双击 **`安装MOD.bat`**；如果已经有 mod 源目录，`启动游戏-本地mod.bat` 也会自动生成 `.mod` 描述文件。
6. 双击 **`启动桥接程序.bat`**，然后双击 **`启动游戏-本地mod.bat`**。

## 路径规则

- `settings.json` 会覆盖 `bridge/config.json` 里的同名字段；普通玩家只改 `settings.json`。
- `mod_source_dir` 推荐使用纯英文、无中文的路径。HOI4/PhysFS 可能无法挂载中文路径下的 mod。
- `navy_out_workdir` 必须包含 `navy.exe` 和 `navy.pck`。
- 如果游戏里出现 `Active Mod Count: 0`，先检查 `mod_source_dir` 和 `.mod` 的 `path=`，再确认没有启用创意工坊的同名副本。

## 手动检查

```bat
cd /d 本文件夹\bridge
python check_setup.py
```

设置检查只读取路径和文件，不会修改存档。
