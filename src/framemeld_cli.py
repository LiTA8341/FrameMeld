"""Translate an FFmpeg-shaped command line into the FrameMeld pipeline."""

from __future__ import annotations

import json
import sys

import insight_blur


CAPABILITIES = {
    "protocol": "org.framemeld.cli",
    "api_version": 1,
    "features": [
        "auto-profile",
        "rife",
        "motion-blur",
        "host-managed-encoder-fallback",
        "structured-status-json-v1",
        "device-diagnostics-json-v1",
    ],
    "license": "GPL-3.0-only",
}

HELP = """\
FrameMeld headless frame-processing mode

Usage:
  ffmpeg.exe -framemeld -i INPUT [FRAMEMELD OPTIONS] OUTPUT

Examples:
  ffmpeg.exe -framemeld -i input.mp4 --interpolate-fps 240 --blur-output-fps 60 output.mp4
  ffmpeg.exe -framemeld -i input.mp4 --performance-mode balanced --blur-output-fps 60 output-auto-to-60.mp4
  ffmpeg.exe -framemeld -i input.mp4 --performance-mode adaptive --blur-output-fps 60 output-adaptive.mp4
  ffmpeg.exe -framemeld -i input.mp4 --config settings.json -c:v h265 -cq 18 output-hevc.mp4

The standard FFmpeg options -y, -hide_banner, -loglevel, -c:v, -cq/-crf,
-c:a, -b:a and the NVENC -gpu selector are accepted. Run with --help-full
to list all processing options. Use -c:v h264 or -c:v h265 for cross-vendor
automatic hardware probing with a same-codec software fallback.

Hosts that already manage encoder retries can pass
--host-managed-encoder-fallback to receive the first attempt's real exit code
instead of allowing FrameMeld to retry silently with a software encoder.

Hosts can pass --status-json-lines to receive machine-readable lifecycle,
device-selection, and failure-domain events on stderr. Each event starts with
the stable "framemeld-status:" prefix.
The optional --host-encoder-adapter-json value records the host-planned adapter
without claiming that a system-default encoder was explicitly bound to it.

Performance modes:
  --performance-mode original   Upstream-compatible full RIFE and deduplication
  --performance-mode exact      Preserve the configured interpolation target
  --performance-mode balanced   Use verified source-aware profiles (default)
  --performance-mode adaptive   Skip RIFE only on deterministic near-static pairs

Balanced/adaptive use explicit settings first, then verified frame-rate
profiles, then the generic integer-multiple fallback. The recognition tolerance
does not round the source timeline. Explicit --blur-amount always wins.
"""


def capabilities_json() -> str:
    return json.dumps(CAPABILITIES, separators=(",", ":"), sort_keys=True)


def translate(argv: list[str]) -> list[str]:
    if not argv or any(item in {"-h", "--help"} for item in argv):
        print(HELP)
        raise SystemExit(0)
    if "--help-full" in argv:
        insight_blur.parser().print_help()
        raise SystemExit(0)

    try:
        input_index = argv.index("-i")
        source = argv[input_index + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("FrameMeld mode requires exactly one '-i INPUT'") from exc

    remaining = argv[:input_index] + argv[input_index + 2 :]
    if not remaining or remaining[-1].startswith("-"):
        raise ValueError("FrameMeld mode requires OUTPUT as the final argument")
    output = remaining.pop()

    translated: list[str] = []
    index = 0
    while index < len(remaining):
        item = remaining[index]
        if item in {"-y", "-hide_banner"}:
            index += 1
            continue
        mapping = {
            "-loglevel": "--loglevel",
            "-c:v": "--encoder",
            "-codec:v": "--encoder",
            "-vcodec": "--encoder",
            "-cq": "--quality",
            "-crf": "--quality",
            "-c:a": "--audio-codec",
            "-codec:a": "--audio-codec",
            "-acodec": "--audio-codec",
            "-b:a": "--audio-bitrate",
            "-gpu": "--encoder-device",
        }
        if item in mapping:
            if index + 1 >= len(remaining):
                raise ValueError(f"Missing value after {item}")
            translated.extend([mapping[item], remaining[index + 1]])
            index += 2
            continue
        translated.append(item)
        index += 1
    return [source, output, *translated]


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--capabilities-json"]:
        print(capabilities_json())
        return 0
    try:
        return insight_blur.main(translate(arguments))
    except ValueError as exc:
        print(f"framemeld: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
