# Sky Music Automation

[English](README.md)

PC《Sky 光·遇》自动演奏工具 —— 导入 MIDI，自动弹奏。

## 功能一览

| 模块 | 说明 |
|------|------|
| **轨道分析** | 加载 MIDI 文件，查看轨道列表、音符分布、调性检测（自动推荐移调值） |
| **谱面转换** | MIDI → 游戏谱面 JSON，支持移调、八度偏移、就近吸附 |
| **AI 智能编曲** | 通过 OpenAI 兼容 API 将超出乐器音域的音符智能重映射；支持流式响应、三档风格（保守/平衡/自由）、两步审阅确认 |
| **预览 MIDI** | 转换后生成预览 MIDI，用系统播放器试听转换效果 |
| **轨道试听** | 导出指定轨道为独立 MIDI 并播放 |
| **自动演奏** | 加载谱面后自动发送键盘输入，半透明浮窗实时显示进度和按键 |
| **模拟演奏** | 可视化 3×5 键盘，带音频反馈，可调速度和移调 |
| **空跑调试** | 不发送按键，仅通过浮窗预览完整演奏流程 |
| **按键映射** | GUI 可视化编辑音符 → 按键映射，支持多乐器配置 |
| **自动更新** | 启动时自动检查 GitHub Release，一键下载更新 |
| **双语 / 双主题** | 中文 / English，深色 / 浅色主题 |

## 快速开始

### 方式一：下载打包版（推荐）

前往 [Releases](../../releases) 下载最新的 `SkyMusicAutomation-windows.zip`，解压后双击 `SkyMusicAutomation.exe` 即可运行。

### 方式二：从源码运行

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[input,gui,ai]"
python -m src.interfaces.gui.app
```

需要 Python ≥ 3.11。

## 使用流程

```
① 轨道  →  ② 转换  →  ③ AI 编曲（可选）  →  ④ 预览  →  ⑤ 演奏
```

1. **轨道** — 选择 MIDI 文件，查看各轨道信息和调性分析
2. **转换** — 选择轨道和映射配置，设置移调 / 八度参数，转换为谱面 JSON；可勾选「生成预览 MIDI」试听效果
3. **AI 编曲**（可选）— 填入 API Key，选择模式和风格，点击「AI 编曲」：
   - AI 返回分析方案和映射建议，用户可逐条审核修改
   - 满意后点击「应用映射」，或输入补充指令点击「带反馈重试」
4. **预览** — 试听指定轨道确认效果
5. **演奏** — 加载谱面，点击「开始演奏」，在倒计时内切换到游戏窗口

> 建议先用「空跑模式」测试，确认节奏和按键正常后再实际演奏。

## AI 编曲

支持两种模式：

- **音符重映射**（快速）— 同一音符始终映射到相同替代音
- **上下文编曲**（智能）— 分析旋律上下文，同一音符在不同位置可映射到不同替代音

三档风格：

| 风格 | 行为 |
|------|------|
| 严格替换 | 每个未映射音都必须替换，忠于原曲 |
| 智能改编 | 允许丢弃装饰音 / 经过音，简化和弦 |
| 自由改编 | 完全自由创作，优先保证音乐性 |

AI 编曲采用两步确认流程：AI 先输出分析方案和映射建议 → 用户审阅修改 → 确认应用或带反馈重试。

兼容任何 OpenAI 格式的 API 端点（OpenAI / DeepSeek / 本地模型等）。

## 演奏浮窗

演奏时屏幕右上角会出现半透明浮窗（可拖拽）：

- 进度条与已用 / 总时间
- 当前正在按的键（高亮）
- 即将按的键（未来 3 秒预览）
- **F9** 全局热键随时停止

浮窗不会抢占游戏焦点，空跑和实际演奏均会显示。

## 模拟演奏

模拟演奏标签页提供可视化 3×5 光遇键盘，带音频反馈：

- **自动/手动模式** — 自动回放谱面或使用键盘自由演奏
- **速度调节** — 0.25x 到 2.0x 播放速度
- **移调** — 所有音符升降 ±12 半音
- **自定义采样** — 在 `assets/instruments/piano/` 目录放置 WAV 文件可替换内置钢琴合成音色（详见[采样说明](assets/instruments/piano/README.md)）

## 按键映射

编辑 `configs/mapping.example.yaml` 或在 GUI「按键映射」标签页中修改：

```yaml
default_profile: default
profiles:
  default:
    note_to_key:
      '60': y    # C4
      '62': u    # D4
      '64': i    # E4
      # ...
    transpose_semitones: 0
    octave_shift: 0
```

- 每个 profile 对应一种乐器的音符 → 按键映射
- 支持移调（`transpose_semitones`）和八度偏移（`octave_shift`）
- 未映射的音符可通过「就近吸附」自动匹配最近的可用音

## CLI

安装后可用 `skytool` 命令行工具：

```bash
skytool tracks midis/song.mid
skytool convert midis/song.mid -m configs/mapping.example.yaml -o output/chart.json
skytool play output/chart.json --dry-run
```

## 项目结构

```
src/
├── domain/          # 领域模型（ChartDocument, MappingConfig 等）
├── application/     # 业务逻辑（converter, player, ai_arranger, updater）
├── infrastructure/  # 基础设施（MIDI 读取, 文件存储）
└── interfaces/
    ├── cli/         # 命令行界面（Typer）
    └── gui/         # 图形界面（PySide6）
        ├── tabs/    # 各功能标签页
        └── workers/ # 后台线程 Worker
```

## 构建发布

推送 tag 即自动构建并发布到 GitHub Releases：

```bash
git tag v0.2.0
git push --tags
```

GitHub Actions 会自动从 tag 提取版本号、打包 PyInstaller 产物并创建 Release。

## 风险提示

《Sky 光·遇》为在线游戏，自动化输入可能违反服务条款并带来账号风险。请自行判断，谨慎使用。
