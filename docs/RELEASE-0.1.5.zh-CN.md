# FrameMeld 0.1.5

本版本基于 0.1.4-fast.2 的 Fast 策略，保留补帧、去重、动态模糊权重和非整数帧率采样方式。

- 内置 FFmpeg/FFprobe 升级至经过 SHA-256 校验的 9.0.2 full build，修复旧内核在部分音视频任务中提前截断流却返回成功的问题。
- 增加独立锐化能力 `independent-sharpen-v1` 和产品版本能力字段 `version: 0.1.5`。
- 锐化使用现有 3×3 亮度通道 unsharp，不改变色度；默认关闭。`--final-sharpen 0.15` 开启轻度锐化，`0` 关闭。
- `--sharpen-only --final-sharpen 0.15` 仅锐化，禁用补帧、去重、动态模糊、变速和颜色调整，保持输入帧率。
- 默认帧混合与锐化独立：仅帧混合不附加锐化，同时启用时在帧混合完成后锐化一次。
- 新版 Insight Agent 的锐化滑条范围为 0.10–0.30，默认 0.15；运行时 CLI 仍兼容原有 0–1.5 参数范围。

请下载完整运行时，并在宿主中选择新目录顶层的 `ffmpeg.exe`。不要单独覆盖顶层启动器。
旧版宿主可能仍显式传入 0.15，独立开关需要配套的新版 Insight Agent。

普通 FFmpeg 参数转发、`-framemeld`、`-blur` 和 `org.framemeld.cli` API 1 均保留。
验证脚本 `scripts/verify-runtime.py` 检查版本、内核散列和无 GPU 静默截断回归；
`scripts/smoke-test.ps1` 与 `scripts/verify-engine.ps1` 检查实际专用管线。
