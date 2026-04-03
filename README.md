# Sky Music Automation

PC《Sky 光遇》自动演奏工具 —— 导入 MIDI，自动弹奏。

## 功能

- 加载 MIDI 文件，查看轨道列表与调性分析
- 将 MIDI 转换为游戏可用的谱面（自动推荐移调）
- 试听指定轨道（系统播放器）
- 自动键盘演奏（支持空跑调试）
- 可视化编辑按键映射配置
- 中文 / English 双语界面，深色 / 浅色主题

## 使用方式

### 方式一：下载打包版（推荐）

前往 [Releases](../../releases) 下载最新的 `SkyMusicAutomation-windows.zip`，解压后双击 `SkyMusicAutomation.exe` 即可运行（自动请求管理员权限）。

### 方式二：从源码运行

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[input,gui]"
python -m src.interfaces.gui.app
```

## 使用流程

1. **轨道** — 选择 MIDI 文件，查看各轨道信息和调性
2. **转换** — 选择轨道和映射配置，转换为谱面 JSON
3. **预览** — 试听指定轨道（可选）
4. **演奏** — 加载谱面，切换到游戏窗口，自动弹奏

> 建议先用「空跑模式」测试，确认节奏正常后再实际演奏。

## 按键映射

编辑 `configs/mapping.example.yaml` 或在 GUI 的「按键映射」标签页中修改：

- 每个配置对应一种乐器的音符到按键映射
- 支持移调和八度偏移
- 未映射的音符可通过「就近吸附」自动匹配最近的可用音

## CLI

同时提供命令行工具，安装后可用 `skytool` 命令：

```bash
skytool tracks midis/song.mid
skytool convert midis/song.mid -m configs/mapping.example.yaml -o output/chart.json
skytool play output/chart.json --dry-run
```

## 风险提示

《Sky 光遇》为在线游戏，自动化输入可能违反服务条款并带来账号风险。请自行判断，谨慎使用。
