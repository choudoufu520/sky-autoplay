# Sky Music Automation (CLI First)

面向 PC《Sky 光遇》的自动演奏工具链（首期无 GUI）。

## 功能

- `tracks`: 查看 MIDI 音轨列表与摘要
- `convert`: MIDI -> Chart JSON
- `preview-track`: 指定轨道导出并直接打开（系统播放器试听）
- `preview-track-game`: 指定轨道转换后在游戏键位回放（可 dry-run）
- `play`: Chart JSON -> 键盘输入回放

## 架构

采用分层架构，便于后续扩展 GUI 或替换输入后端：

- `src/domain`: 核心模型（Chart、Mapping）
- `src/application`: 转换与播放用例
- `src/infrastructure`: MIDI 读取、YAML/JSON 仓储、输入适配器
- `src/interfaces/cli`: CLI 入口（Typer）

## 快速开始

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e ".[input,dev]"
```

## 常用命令

### 1) 查看 MIDI 音轨

```bash
skytool tracks midis/song.mid
```

### 2) 转换 MIDI 为 Chart

```bash
skytool convert midis/song.mid -m configs/mapping.example.yaml -o output/chart.json --profile guitar
```

常用参数：

- `--strict`: 任意未映射音符即失败
- `--transpose`: 半音移调
- `--octave`: 八度偏移
- `--note-mode`: `tap` 或 `hold`
- `--single-track`: 指定读取轨道

### 3) 指定轨道音乐试听（系统播放器）

```bash
skytool preview-track midis/song.mid -t 1
```

若你想保留导出的轨道 MIDI：

```bash
skytool preview-track midis/song.mid -t 1 -o output/track1.mid --no-open
```

### 4) 指定轨道在游戏里回放（键位映射）

```bash
skytool preview-track-game midis/song.mid -t 1 -m configs/mapping.example.yaml --profile guitar --dry-run --debug
```

真实落键回放（谨慎）：

```bash
skytool preview-track-game midis/song.mid -t 1 -m configs/mapping.example.yaml --profile guitar --no-dry-run --no-debug
```

### 5) 播放 Chart

先 dry-run 看时序，再落键：

```bash
skytool play output/chart.json --dry-run --debug
skytool play output/chart.json -c configs/play.example.yaml
```

管理员方式启动（Windows）：

```powershell
cd D:\work\df-guance
Start-Process -Verb RunAs -FilePath "python" -WorkingDirectory "D:\work\df-guance" -ArgumentList `
  "-m","src.interfaces.cli.main","play","output\chart.json","-c","configs\play.example.yaml"
```

## 映射文件（核心）

见 `configs/mapping.example.yaml`，支持：

- 多 profile（如 `lyre`、`piano`、`guitar`）
- 每个 profile 单独不完整音阶
- 可选移调/八度偏移
- 可选 `program_to_profile`（若 MIDI 使用 Program Change）

## 合规与风险

《Sky 光遇》为在线游戏，自动化输入可能违反服务条款并带来账号风险。请先确认平台与游戏规则，谨慎自担使用风险。