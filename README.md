# FrameMeld

FrameMeld builds a Windows, headless, FFmpeg-compatible runtime for GPU frame
interpolation, duplicate-frame repair, motion blur, time scaling, and color
processing. Ordinary FFmpeg and FFprobe commands are forwarded unchanged;
FrameMeld processing is activated only by the explicit `-framemeld` command.
FFplay is intentionally not bundled because FrameMeld has no interactive
playback UI and downstream integrations use only FFmpeg and FFprobe.

The runtime uses a pinned FFmpeg full build, VapourSynth R70, RIFE 4.26 through
NCNN Vulkan, and compatibility plugins for SVPFlow, MVTools, L-SMASH Source,
BestSource, Akarin, and Vapoursynth-adjust. NVIDIA, AMD, and Intel GPUs use the
same interpolation model and processing policy.

## Build on Windows

Requirements:

- Windows 10 or later
- PowerShell 5.1 or later
- 7-Zip
- Visual Studio Build Tools with the x64 C++ toolchain
- Network access for pinned dependencies, unless they are already cached

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-runtime.ps1
```

The build is written to `dist\framemeld-runtime`. Every download is pinned by
SHA-256 in `config\windows-runtime.json`.

Run the unit tests and runtime checks with:

```powershell
python -m unittest discover -s tests -v
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-test.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\verify-engine.ps1
```

## Command line

Normal FFmpeg commands work without a FrameMeld flag:

```powershell
dist\framemeld-runtime\ffmpeg.exe -i input.mp4 -c:v libx265 output.mp4
```

Add `-framemeld` immediately after `ffmpeg.exe` to use the processing engine:

```powershell
dist\framemeld-runtime\ffmpeg.exe -framemeld `
  -i input-240fps.mp4 --performance-mode balanced --blur-output-fps 60 `
  --weighting vegas -c:v h265 -cq 18 output.mp4
```

`-blur` remains available as a legacy alias. New integrations should discover
the stable protocol with:

```powershell
dist\framemeld-runtime\ffmpeg.exe -framemeld --capabilities-json
```

The response identifies protocol `org.framemeld.cli`, API version 1, supported
features, and the `GPL-3.0-only` license boundary.

Use `-c:v h264` or `-c:v h265` for automatic NVIDIA/AMD/Intel hardware probing
with a same-codec software fallback. Explicit encoder names such as
`hevc_nvenc`, `hevc_amf`, `hevc_qsv`, and `libx265` remain available.

## Automatic frame-rate policy

Explicit `--interpolate-fps`, `--blur-amount`, and sample settings always win.
Otherwise `balanced` and `adaptive` select the verified source family within a
±0.5 FPS recognition tolerance while retaining the source's exact rational
rate:

| Input family | Intermediate timeline | Motion-blur profile |
| ---: | ---: | --- |
| below 56 FPS | Smallest integer multiple reaching at least 200 FPS | 60 FPS profile: centered 5-tap Vegas, amount 1.0 |
| 60 FPS | source × 5 | centered 5-tap Vegas, amount 1.0 |
| 90 FPS | source × 4 | centered 5-tap Vegas, amount 1.0 |
| 120 FPS | source × 3 | continuous 5-to-7-tap mix, amount 0.85 |
| 144 FPS | fixed 360 FPS | continuous 5-to-7-tap mix, amount 0.925 |
| 180 FPS | fixed 360 FPS | continuous 5-to-7-tap mix, amount 0.925 |
| 240 FPS | source × 2 (480 FPS) | centered profile, amount 1.0 |
| other 56 to below 300 FPS | Smallest integer multiple reaching at least 300 FPS | centered profile, amount 1.0 |
| 300 FPS and above | native timeline; bypass main RIFE interpolation | centered profile, amount 1.0 |

`adaptive` uses the same output timeline but skips RIFE inference for
deterministically detected near-static frame pairs. `exact` preserves the
configured interpolation target, and `original` retains the upstream-compatible
full interpolation and duplicate-analysis path.

More examples and parameter notes are in `USAGE.zh-CN.md` and
`docs/ARCHITECTURE.zh-CN.md`.

## License and upstream work

FrameMeld is released under `GPL-3.0-only`. Its behavior was developed from and
compared against the GPLv3 Blur project at the fixed revision documented in
`NOTICE.md`. Downloaded and bundled components retain their own notices and
licenses. See `LICENSE`, `NOTICE.md`, and `config/windows-runtime.json` before
redistributing a runtime.
