# FrameMeld 架构

## 运行边界

顶层 `ffmpeg.exe` 是一个轻量 Windows 启动器：普通命令转发给固定版本的
`ffmpeg-core.exe`，`ffprobe.exe` 转发给上游 FFprobe；只有 `-framemeld`
（以及兼容别名 `-blur`）会进入 Python/VapourSynth 帧处理管线。

```text
ffmpeg.exe
├─ 普通参数 ──────────────> lib/ffmpeg/ffmpeg-core.exe
└─ -framemeld
   └─ framemeld_cli.py
      ├─ 媒体探测与参数规划
      ├─ source: BestSource / L-SMASH
      ├─ analysis: 低分辨率亮度差异
      ├─ deduplicate: RIFE / SVP / MVTools
      ├─ interpolate: NCNN Vulkan RIFE / SVPFlow
      ├─ motion blur: Akarin 表达式与惰性帧图
      ├─ timescale / color adjust
      └─ Y4M 管道 ────────> FFmpeg NVENC/AMF/QSV/x264/x265
```

FrameMeld 通过 `-framemeld --capabilities-json` 提供稳定的
`org.framemeld.cli` API 1 探测协议。宿主程序应先探测该协议，再决定是否显示
或启用帧混合功能；普通 FFmpeg 功能不依赖这个协议。

## 模块

- `src/ffmpeg_launcher.cpp`：FFmpeg/FFprobe 兼容入口与 FrameMeld 路由。
- `src/framemeld_cli.py`：FFmpeg 形状参数到处理管线参数的转换。
- `src/insight_blur.py`：媒体探测、命令规划、子进程与编码失败回退。
- `src/engine_defaults.py`：预设、自动帧率策略和运行时配置生成。
- `src/engine/insight_engine/`：分析、去重、插值、运动模糊和帧图组装。
- `src/encoder_selection.py`：跨厂商硬件编码探测与同编码族软件回退。

内部模块名保留了早期开发阶段的兼容名称；对外品牌、命令协议和分发目录统一为
FrameMeld。

## 上游参考和构建输入

本项目的行为开发和对照使用了 `f0e/blur` 的固定 GPLv3 提交
`6fd0eccf7bf1c142a80473a8b0b937558e498975`。生成运行时不复制本地
`blur-master/src/vapoursynth`，构建也不依赖 `blur-master` 目录。确切来源与
声明见根目录 `NOTICE.md`。

所有可下载依赖都在 `config/windows-runtime.json` 中固定版本、URL 和 SHA-256。
构建脚本验证哈希后才解压，并把 FrameMeld 的 GPLv3 许可证和声明复制到发行目录。

## 决策与性能约束

Fast 分支以 `source-relative-fast-v1` 重定义内部 `balanced` 自动表，但不改变
FrameMeld 的 CLI/API。这样 Insight Agent 无需感知版本；普通版与 Fast 版通过
完整运行时目录隔离，不能混合替换其中的可执行文件、Python 工具或帧引擎模块。

RIFE 的 RGBS 转换、模型参数、GPU 索引和 Vulkan 调用保持固定；NVIDIA、AMD、
Intel 使用同一套 NCNN Vulkan RIFE 4.26 模型。编码器选择与补帧算法相互独立。
低分辨率分析只影响是否跳过近静止帧对，不改变输出帧数。帧率族容差只决定分支，
不会改写探测到的精确有理帧率。Fast 自动表为 60→240、90→270、120→240、
144→288、180→360、240→240，240 FPS 以上保持原生；56–240 FPS 的非标准输入
使用精确 240 FPS 目标，低于 56 FPS 则继续使用至少 200 FPS 的整数倍下限。

运动模糊之后的非整数降采样使用相位感知的线性采样。短周期比率用周期性
`SelectEvery` 图，长周期分数帧率用 `FrameEval`，两条路径都在精确输出时刻
混合相邻时间轴样本，避免固定向下取整引入周期性相位误差。
