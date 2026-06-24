# POP Planner SCSCL Tools

本仓库基于 Feetech `FTServo_Python` SDK，保留 `scservo_sdk` 原始通信库，并在 `scscl/` 下扩展了 SCSCL 舵机的扫描、参数配置、手动控制和三次曲线轨迹规划工具。

## 环境

- Python 3.8+
- Linux 串口设备，例如 `/dev/ttyUSB0`、`/dev/ttyACM0`
- Python 依赖：

```bash
pip install pyserial PyQt5 numpy scipy matplotlib
```

`scipy` 可选；缺失时三次轨迹会自动使用 `numpy` 多项式求解。

## 快速启动

进入仓库根目录后运行：

```bash
python3 scscl/cubic_trajectory_run.py
```

典型流程：

1. 选择串口和波特率，默认波特率为 `500000`。
2. 点击 `扫描ID` 获取在线 SCSCL 舵机。
3. 选中 ID 后，上位机会自动读取电机内部配置并显示到参数框。
4. 在 `手动模式` 中勾选目标 ID，拖动滑块给位；`反向` 使用相对位移逻辑，切换时不会强制跳回旧位置。
5. 在 `单电机控制` 或 `多电机控制` 中打开悬浮菜单，配置单点或往复轨迹并执行。

## scscl 文件说明

| 文件 | 功能 |
| --- | --- |
| `cubic_trajectory_run.py` | PyQt 上位机入口。 |
| `cubic_trajectory_ui.py` | 主界面、串口扫描、参数配置、手动控制、多电机轨迹执行逻辑。 |
| `cubic_trajectory.py` | SCSCL 寄存器定义、串口扫描、读写校验、配置目录和轨迹模式公共逻辑。 |
| `cubic_trajectory_planner.py` | 三次 Hermite/多项式轨迹生成和采样。 |
| `ping.py` | 命令行串口选择、ID 扫描和 ping 测试。 |
| `read.py` / `write.py` / `read_write.py` | SDK 原始读写示例。 |
| `sync_write.py` / `reg_write.py` | 同步写、寄存写示例。 |
| `wheel.py` | PWM/轮模式示例。 |
| `assets/combobox_down_arrow.svg` | 上位机下拉框箭头资源。 |

## 参数与日志

- `config/`：导出的电机配置 JSON。
- `parameter/`：导出的轨迹参数 JSON。
- `log/`：上位机运行日志。

这些目录为运行时产物，默认不纳入版本控制。需要保留参数时，可通过界面导出 JSON 后单独备份。

## 注意事项

- `写入电机` 会修改舵机内部配置，确认 ID、角度限制和保护参数后再执行。
- 多电机往复控制支持不同 ID 独立配置；切换当前编辑 ID 时，配置会按 ID 分离保存。
- 往复轨迹在启动时会读取当前实际位置，先连续过渡到目标端点，再进入循环，避免调换起终点时发生跳变。
