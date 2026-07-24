# 蟑螂桌宠（Cockroach Pet）

透明置顶桌面宠物：平时躲角落，互动才出来；含打工人 / 财务黑话与系统监控。

支持：**macOS**（命令行）· **Windows**（命令行 / exe）

## 命令行运行

### macOS

```bash
pip3 install -r requirements-mac.txt
python3 cockroach_pet.py
# 或 ./run.sh
```

### Windows

```bat
pip install -r requirements.txt
python cockroach_pet.py
REM 或 run.bat
```

按 **Esc** 退出。焦点快捷键仍需先点蟑螂；**全局热键 / 菜单栏(托盘) 无需焦点**。

完整操作见 [操作说明.md](操作说明.md)。

## 产品功能（v0.1–v0.3）

| 能力 | 说明 |
|------|------|
| 设置 | 工作目录下 `settings.json`（气泡、提醒、穿透、话术包、皮肤、热键） |
| 进度 | `progress.json`（亲密度、成就称号、已解锁皮肤） |
| 菜单栏/托盘 | Mac 菜单栏 🪳 · Win 系统托盘：召唤、总览、穿透、开关提醒、换皮肤、退出 |
| 全局热键 | `Ctrl+Alt+R` 召唤 · `/` 总览 · `P` 穿透 · `S` 状态 · `Q` 退出（Mac 需辅助功能权限） |
| 话术包 | `packs/*.json`，可改文案后菜单「重载话术包」 |
| 监控表演 | CPU/内存高等级时旋转、缩身、特效联动 |
| 皮肤 | `=` 或菜单切换：`default` / `gold` / `ghost`（也可放 `skins/*.png`） |
| 双宠 | 对喷时金色会计现身互怼，平时仅主宠；`,` / `Ctrl+Alt+B` 手动开吵 |

## 打包 Windows exe

本机是 **macOS** 时，请用 GitHub Actions 在云端 Windows 上打包（推荐）：

1. 打开仓库 **Actions** → 左侧 **Build Windows EXE**
2. 点 **Run workflow**（或 push 到 `main` 自动触发）
3. 等绿勾完成后，进入该次运行页面，底部 **Artifacts** 下载 `cockroach-pet-exe`
4. 解压得到 `cockroach_pet.exe`，拷到 Windows 双击运行

若已有 Windows 电脑，也可本地打包：

```bat
build_win.bat
```

产物：`dist\cockroach_pet.exe`。

## 快捷键速查

常用：`H` 帮助 · `-` 穿透 · `=` 皮肤 · `G`/`J` 打工 · `/` 监控总览 · `Esc` 退出。

详见 **[操作说明.md](操作说明.md)**。
