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

Device-aware hosts can query the bounded Vulkan inventory without starting an
export:

```powershell
dist\framemeld-runtime\ffmpeg.exe -framemeld --device-inventory-json
```

The response deliberately labels FFmpeg Vulkan and RIFE/ncnn Vulkan as
different index spaces. A host may pass a uniquely matched candidate with
`--gpu INDEX` and keep its intended RIFE adapter metadata separate in
`--host-rife-adapter-json`. During an export, `rife_binding` reports the device
banner actually emitted by ncnn. This runtime observation can validate a
host-side success cache without claiming that FFmpeg and ncnn enumeration are
universally identical.

Hosts may opt into `structured-status-json-v1` with `--status-json-lines`.
FrameMeld then emits prefixed JSON lifecycle, frame-progress, selected-device,
child-return-code, and failure-domain events on stderr. Optional host adapter
metadata is echoed separately from the runtime's binding status so a planned
adapter is never reported as an explicitly bound device.
The device event classifies QSV as a separate Intel branch. QSV and AMF are
currently reported as `system-default`; NVENC retains its explicit `-gpu`
binding. Hosts should branch on the selected encoder backend (`*_qsv` or
`*_amf`), not on a hard-coded GPU model list.

The optional `device-diagnostics-json-v1` records the FFmpeg Vulkan inventory,
a conservative DXGI-to-RIFE mapping candidate, encoder initialization/binding
evidence, first-frame and first-output-packet observations, and a final
performance summary. FFmpeg's Vulkan listing does not currently expose UUID or
Windows LUID, so mappings without a stable identity are explicitly reported as
`candidate`, `ambiguous`, `unmatched`, or `unknown` rather than exact.

Use `-c:v h264` or `-c:v h265` for automatic NVIDIA/AMD/Intel hardware probing
with a same-codec software fallback. Explicit encoder names such as
`hevc_nvenc`, `hevc_amf`, `hevc_qsv`, and `libx265` remain available.
All H.264 paths explicitly emit High Profile with 8-bit YUV 4:2:0 for video
platform and social-media delivery; HEVC paths retain their Main defaults.

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
