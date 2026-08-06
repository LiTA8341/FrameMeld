# FrameMeld 使用说明

## 基本用法

构建完成后的入口是：

```text
dist\framemeld-runtime\ffmpeg.exe
```

普通 FFmpeg/FFprobe 参数会原样转发。只有显式添加 `-framemeld` 时才会进入
FrameMeld 帧处理管线，因此同一个可执行文件仍可用于普通转码、封装和探测。
`-blur` 仅作为旧版兼容别名保留。

```powershell
dist\framemeld-runtime\ffmpeg.exe -framemeld `
  -i input.mp4 `
  --performance-mode balanced `
  --blur-output-fps 60 `
  -c:v h265 -cq 18 `
  output.mp4
```

查看 FrameMeld 协议能力和全部参数：

```powershell
dist\framemeld-runtime\ffmpeg.exe -framemeld --capabilities-json
dist\framemeld-runtime\ffmpeg.exe -framemeld --help-full
```

## 性能模式

- `original`：上游兼容的完整 RIFE 补帧与全分辨率重复帧分析。
- `exact`：保留配置中的固定 FPS 或倍数目标。
- `balanced`：使用已完成主观确认的自动时间轴和模糊策略，默认推荐。
- `adaptive`：时间轴与 `balanced` 相同，仅对近静止帧对跳过无收益的 RIFE 推理。

显式 `--interpolate-fps`、`--blur-amount` 和 `--performance-samples` 的优先级
最高。自动分支以 ±0.5 FPS 容差识别 60/90/120/144/180/360 等专用帧率族，
但计算始终保留源素材的精确分数帧率，不会先把 59.94、119.88 等数值取整。

## 已确认的自动策略

| 输入帧率 | 自动中间时间轴 | 模糊策略 |
| ---: | ---: | --- |
| `<56` | 最小整数倍补到至少 200 FPS | 复用 60 FPS：居中 5 抽头 Vegas，amount 1.0 |
| 60 | 源帧率 ×5 | 居中 5 抽头 Vegas，amount 1.0 |
| 90 | 源帧率 ×4 | 居中 5 抽头 Vegas，amount 1.0 |
| 120 | 源帧率 ×3 | 5→7 抽头连续混合，amount 0.85 |
| 144 | 固定 360 FPS | 5→7 抽头连续混合，amount 0.925 |
| 180 | 固定 360 FPS | 5→7 抽头连续混合，amount 0.925 |
| 240 | 源帧率 ×2，即 480 FPS | 居中策略，amount 1.0 |
| 其他 56–`<300` | 最小整数倍补到至少 300 FPS | 居中策略，amount 1.0 |
| `>=300` | 保持输入时间轴，跳过主 RIFE 补帧 | 居中策略，amount 1.0 |

120 FPS 的 `amount=0.85` 是 5 抽头与 7 抽头结果之间 40% 的连续插值；
144/180 FPS 的 `amount=0.925` 对应 70% 插值。两者都保持居中，因此不会引入
偶数抽头常见的半帧时间偏移。

## 常用命令

只补帧、不添加运动模糊：

```powershell
dist\framemeld-runtime\ffmpeg.exe -framemeld `
  -i input.mp4 --interpolate-fps 120 --no-blur `
  -c:v h264 output-120fps.mp4
```

使用 JSON 配置：

```powershell
dist\framemeld-runtime\ffmpeg.exe -framemeld `
  -i input.mp4 `
  --config dist\framemeld-runtime\rife-performance.json `
  output.mp4
```

`-c:v h264` 与 `-c:v h265` 会探测 NVIDIA、AMD、Intel 的对应硬件编码器，
真实短编码测试成功后才开始完整导出；不可用时回退到 `libx264`/`libx265`。
也可以显式指定具体的硬件或软件编码器。

## 构建与验证

```powershell
python -m unittest discover -s tests -v
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-runtime.ps1 -Force
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-test.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\verify-engine.ps1
```
