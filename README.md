# Sky Music Automation

PC《Sky 光·遇》自动演奏工具 —— 导入 MIDI，自动弹奏。

## 功能

- **轨道分析** — 加载 MIDI 文件，查看轨道列表、音符分布与调性检测（自动推荐移调值）
- **谱面转换** — 将 MIDI 转换为游戏谱面，支持移调、八度偏移、就近吸附
- **AI 智能编曲** — 通过 OpenAI 兼容 API 将超出乐器音域的音符智能重映射（流式响应，支持自定义模型与端点）
- **预览 MIDI** — 转换后可生成预览 MIDI 文件，用系统播放器试听转换效果
- **轨道试听** — 导出指定轨道为独立 MIDI 并播放
- **自动演奏** — 加载谱面，自动发送键盘输入；演奏时在屏幕上显示半透明浮窗，实时展示进度、当前 / 即将按键，按 F9 全局热键可随时停止
- **空跑调试** — 不发送按键，仅通过浮窗预览完整演奏流程
- **按键映射编辑** — GUI 可视化编辑音符 → 按键映射，支持多乐器配置
- **双语 / 双主题** — 中文 / English 界面，深色 / 浅色主题切换

## 使用方式

### 方式一：下载打包版（推荐）

前往 [Releases](../../releases) 下载最新的 `SkyMusicAutomation-windows.zip`，解压后双击 `SkyMusicAutomation.exe` 即可运行（自动请求管理员权限）。

### 方式二：从源码运行

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[input,gui,ai]"
python -m src.interfaces.gui.app
```

## 使用流程

1. **轨道** — 选择 MIDI 文件，查看各轨道信息和调性分析
2. **转换** — 选择轨道和映射配置，设置移调 / 八度参数，转换为谱面 JSON；可勾选「生成预览 MIDI」试听转换结果
3. **AI 编曲**（可选）— 填入 API Key，点击「AI 编曲」，AI 会为无法映射的音符推荐最佳替代音
4. **预览** — 试听指定轨道确认效果
5. **演奏** — 加载谱面，点击「开始演奏」，在倒计时内切换到游戏窗口；浮窗实时显示进度和按键提示，按 **F9** 随时停止

> 建议先用「空跑模式」测试，确认节奏和按键正常后再实际演奏。

## 演奏浮窗

演奏时屏幕右上角会出现半透明浮窗（可拖拽），包含：

- 进度条与已用 / 总时间
- 当前正在按的键（高亮）
- 即将按的键（未来 3 秒预览）
- **F9** 停止热键提示

浮窗不会抢占游戏焦点，空跑和实际演奏均会显示。

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

《Sky 光·遇》为在线游戏，自动化输入可能违反服务条款并带来账号风险。请自行判断，谨慎使用。
