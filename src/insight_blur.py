"""Run the pinned Blur VapourSynth graph and encode it with the full FFmpeg build."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

from encoder_selection import SUPPORTED_ENCODER_REQUESTS, available_encoders, encoder_args, select_encoder
from engine_defaults import apply_performance_policy, merge_settings


def runtime_root() -> Path:
    return Path(__file__).resolve().parent.parent


def parse_fraction(value: str) -> Fraction:
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise RuntimeError(f"Invalid frame rate reported by ffprobe: {value}") from exc
    if result <= 0:
        raise RuntimeError(f"Invalid non-positive frame rate: {value}")
    return result


def interpolation_target(value: str, source_rate: Fraction) -> Fraction:
    text = str(value).strip().lower()
    return source_rate * parse_fraction(text[:-1]) if text.endswith("x") else parse_fraction(text)


def probe_video(ffprobe: Path, source: Path) -> dict[str, Any]:
    command = [
        str(ffprobe),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate,color_range,sample_aspect_ratio",
        "-of", "json",
        str(source),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ffprobe failed").strip())
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError("Input has no video stream")
    stream = streams[0]
    rate = parse_fraction(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")
    return {
        "fps_num": rate.numerator,
        "fps_den": rate.denominator,
        "color_range": stream.get("color_range") or "tv",
    }


def atempo_chain(speed: float) -> str:
    if speed <= 0:
        raise ValueError("Audio tempo must be positive")
    parts: list[str] = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.8f}")
    return ",".join(parts)


def audio_filter(settings: dict[str, Any], sample_rate: int = 48000) -> str | None:
    if not settings["timescale"]:
        return None
    parts: list[str] = []
    input_scale = float(settings["input_timescale"])
    output_scale = float(settings["output_timescale"])
    if abs(input_scale - 1.0) > 1e-9:
        parts.extend([f"asetrate={sample_rate / input_scale:.8f}", f"aresample={sample_rate}"])
    if abs(output_scale - 1.0) > 1e-9:
        if settings["output_timescale_audio_pitch"]:
            parts.extend([f"asetrate={sample_rate * output_scale:.8f}", f"aresample={sample_rate}"])
        else:
            parts.append(atempo_chain(output_scale))
    return ",".join(parts) or None


def build_commands(
    args: argparse.Namespace,
    *,
    encoder_override: str | None = None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    root = runtime_root()
    lib = root / "lib"
    ffmpeg = lib / "ffmpeg" / "ffmpeg-core.exe"
    ffprobe = lib / "ffmpeg" / "ffprobe.exe"
    vspipe = lib / "vapoursynth" / "VSPipe.exe"
    engine_script = lib / "engine_entry.py"
    model = lib / "models" / "rife-v4.26_ensembleFalse"
    for required in (ffmpeg, ffprobe, vspipe, engine_script, model / "flownet.bin", model / "flownet.param"):
        if not required.exists():
            raise FileNotFoundError(f"Runtime file is missing: {required}")

    source = args.input.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Input does not exist: {source}")
    if source == output:
        raise ValueError("Input and output paths must differ")
    output.parent.mkdir(parents=True, exist_ok=True)
    info = probe_video(ffprobe, source)
    overrides: dict[str, Any] = {}
    if args.engine_preset:
        preset_path = root / "presets" / f"{args.engine_preset}.json"
        if not preset_path.is_file():
            raise FileNotFoundError(f"Engine preset is missing: {preset_path}")
        preset_values = json.loads(preset_path.read_text(encoding="utf-8"))
        if not isinstance(preset_values, dict):
            raise ValueError(f"Engine preset root must be an object: {preset_path}")
        overrides.update(preset_values)
    if args.config:
        config_values = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config_values, dict):
            raise ValueError("Config root must be a JSON object")
        overrides.update(config_values)
    cli_values = {
        "interpolate": False if args.no_interpolate else None,
        "interpolated_fps": args.interpolate_fps,
        "interpolation_method": args.interpolation_method,
        "pre_interpolate": True if args.pre_interpolate_fps is not None else None,
        "pre_interpolated_fps": args.pre_interpolate_fps,
        "blur": False if args.no_blur else None,
        "blur_output_fps": args.blur_output_fps,
        "blur_amount": args.blur_amount,
        "blur_weighting": args.weighting,
        "blur_gamma": args.blur_gamma,
        "blur_weighting_gaussian_std_dev": args.gaussian_std_dev,
        "blur_weighting_gaussian_mean": args.gaussian_mean,
        "blur_weighting_gaussian_bound": args.gaussian_bound,
        "deduplicate": False if args.no_deduplicate else None,
        "deduplicate_method": args.deduplicate_method,
        "deduplicate_range": args.deduplicate_range,
        "deduplicate_threshold": args.deduplicate_threshold,
        "timescale": True if args.input_timescale is not None or args.output_timescale is not None else None,
        "input_timescale": args.input_timescale,
        "output_timescale": args.output_timescale,
        "output_timescale_audio_pitch": True if args.pitch_shift_audio else None,
        "filters": True if any(value is not None for value in (args.brightness, args.contrast, args.saturation)) else None,
        "brightness": args.brightness,
        "contrast": args.contrast,
        "saturation": args.saturation,
        "gpu_decoding": args.gpu_decoding,
        "gpu_interpolation": args.gpu_interpolation,
        "svp_interpolation_preset": args.svp_preset,
        "svp_interpolation_algorithm": args.svp_algorithm,
        "interpolation_blocksize": args.svp_block_size,
        "interpolation_mask_area": args.svp_mask_area,
        "rife_gpu_index": args.gpu,
        "performance_mode": args.performance_mode,
        "performance_samples": args.performance_samples,
        "adaptive_motion_threshold": args.adaptive_motion_threshold,
        "adaptive_scene_threshold": args.adaptive_scene_threshold,
        "analysis_width": args.analysis_width,
        "quality": args.quality,
    }
    overrides.update({key: value for key, value in cli_values.items() if value is not None})
    explicit_blur_amount = "blur_amount" in overrides
    settings = merge_settings(overrides, model)
    performance = apply_performance_policy(
        settings,
        explicit_interpolation_target=args.interpolate_fps is not None,
        source_fps=Fraction(info["fps_num"], info["fps_den"]),
        explicit_performance_samples=args.performance_samples is not None,
        explicit_blur_amount=explicit_blur_amount,
    )
    quality = int(args.quality if args.quality is not None else settings["quality"])
    if encoder_override is None:
        encoder_plan = select_encoder(ffmpeg, args.encoder)
        encoder = encoder_plan.selected
        encoder_fallback = encoder_plan.fallback
        gpu_vendors = list(encoder_plan.gpu_vendors)
        encoder_attempts = [attempt.as_dict() for attempt in encoder_plan.attempts]
    else:
        if encoder_override not in available_encoders(ffmpeg):
            raise RuntimeError(f"Fallback encoder is unavailable: {encoder_override}")
        encoder = encoder_override
        encoder_fallback = None
        gpu_vendors = []
        encoder_attempts = []

    vspipe_command = [
        str(vspipe), "-p", "-c", "y4m",
        "-a", f"video_path={source}",
        "-a", f"fps_num={info['fps_num']}",
        "-a", f"fps_den={info['fps_den']}",
        "-a", f"color_range={info['color_range']}",
        "-a", f"settings={json.dumps(settings, separators=(',', ':'))}",
        str(engine_script), "-",
    ]
    video_encoder_args = encoder_args(encoder, quality)
    if args.encoder_device is not None and encoder.endswith("_nvenc"):
        video_encoder_args[2:2] = ["-gpu", str(args.encoder_device)]

    ffmpeg_command = [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", args.loglevel, "-stats",
        "-i", "pipe:0", "-fflags", "+genpts", "-i", str(source),
        "-map", "0:v:0", "-map", "1:a?",
        *video_encoder_args,
        "-pix_fmt", "yuv420p",
    ]
    af = audio_filter(settings)
    if af and args.audio_codec == "copy":
        raise ValueError("Audio stream copy cannot be combined with timescale audio filtering")
    if af:
        ffmpeg_command.extend(["-af", af])
    if args.audio_codec == "copy":
        ffmpeg_command.extend(["-c:a", "copy"])
    else:
        ffmpeg_command.extend(["-c:a", "aac", "-b:a", args.audio_bitrate])
    ffmpeg_command.extend(["-movflags", "+faststart", str(output)])
    active_engines: list[str] = []
    if settings["deduplicate"]:
        active_engines.append(f"dedup:{settings['deduplicate_method']}")
    source_rate = Fraction(info["fps_num"], info["fps_den"])
    requested_rate = interpolation_target(settings["interpolated_fps"], source_rate)
    interpolation_executed = bool(settings["interpolate"] and source_rate < requested_rate)
    if interpolation_executed:
        active_engines.append(f"interpolate:{settings['interpolation_method']}")
    if settings["blur"]:
        active_engines.append("motion-blur:akarin")
    return vspipe_command, ffmpeg_command, {
        "engine": ",".join(active_engines) or "passthrough",
        "encoder": encoder,
        "encoder_fallback": encoder_fallback,
        "encoder_device": args.encoder_device if encoder.endswith("_nvenc") else None,
        "audio_codec": args.audio_codec,
        "gpu_vendors": gpu_vendors,
        "encoder_attempts": encoder_attempts,
        "video": info,
        "interpolation": {
            "requested": str(requested_rate),
            "executed": interpolation_executed,
        },
        "performance": performance,
        "settings": settings,
    }


def run_pipeline(vspipe_command: list[str], ffmpeg_command: list[str]) -> tuple[int, int]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    vspipe = subprocess.Popen(vspipe_command, stdout=subprocess.PIPE, creationflags=creationflags)
    assert vspipe.stdout is not None
    ffmpeg = subprocess.Popen(ffmpeg_command, stdin=vspipe.stdout, creationflags=creationflags)
    vspipe.stdout.close()
    try:
        ffmpeg_code = ffmpeg.wait()
    except KeyboardInterrupt:
        ffmpeg.terminate()
        vspipe.terminate()
        raise
    if ffmpeg_code != 0 and vspipe.poll() is None:
        vspipe.terminate()
    vspipe_code = vspipe.wait()
    return ffmpeg_code, vspipe_code


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="GPU RIFE interpolation and Blur-compatible motion blur")
    result.add_argument("input", type=Path)
    result.add_argument("output", type=Path)
    result.add_argument("--config", type=Path, help="JSON object overriding any engine setting")
    result.add_argument("--engine-preset", choices=("speed", "balanced", "quality"), default="balanced")
    result.add_argument(
        "--performance-mode",
        choices=("original", "exact", "balanced", "adaptive"),
        default=None,
        help=(
            "original reproduces upstream Blur's full-RIFE/full-resolution deduplication path; "
            "exact preserves the target with optimized analysis; balanced/adaptive use an automatic timeline"
        ),
    )
    result.add_argument(
        "--performance-samples",
        type=int,
        default=None,
        help="Force a fixed timeline factor per output frame instead of the automatic integer-multiple policy",
    )
    result.add_argument("--adaptive-motion-threshold", type=float, default=None)
    result.add_argument("--adaptive-scene-threshold", type=float, default=None)
    result.add_argument("--analysis-width", type=int, default=None)
    result.add_argument("--interpolate-fps", default=None, help="Target FPS or multiplier such as 4x")
    result.add_argument("--interpolation-method", choices=("rife", "svp"), default=None)
    result.add_argument("--pre-interpolate-fps", default=None, help="Optional RIFE pre-pass target FPS")
    result.add_argument("--no-interpolate", action="store_true")
    result.add_argument("--blur-output-fps", type=int, default=None)
    result.add_argument("--blur-amount", type=float, default=None)
    result.add_argument("--weighting", default=None)
    result.add_argument("--blur-gamma", type=float, default=None)
    result.add_argument("--gaussian-std-dev", type=float, default=None)
    result.add_argument("--gaussian-mean", type=float, default=None)
    result.add_argument("--gaussian-bound", default=None, help="JSON pair such as [0,2]")
    result.add_argument("--no-blur", action="store_true")
    result.add_argument("--no-deduplicate", action="store_true")
    result.add_argument("--deduplicate-method", choices=("rife", "svp", "old"), default=None)
    result.add_argument("--deduplicate-range", type=int, default=None)
    result.add_argument("--deduplicate-threshold", default=None)
    result.add_argument("--input-timescale", type=float, default=None)
    result.add_argument("--output-timescale", type=float, default=None)
    result.add_argument("--pitch-shift-audio", action="store_true", default=None)
    result.add_argument("--brightness", type=float, default=None)
    result.add_argument("--contrast", type=float, default=None)
    result.add_argument("--saturation", type=float, default=None)
    result.add_argument("--gpu-decoding", action=argparse.BooleanOptionalAction, default=None)
    result.add_argument("--gpu-interpolation", action=argparse.BooleanOptionalAction, default=None)
    result.add_argument("--svp-preset", choices=("weak", "film", "smooth", "animation", "default", "test"), default=None)
    result.add_argument("--svp-algorithm", type=int, default=None)
    result.add_argument("--svp-block-size", type=int, choices=(4, 8, 16, 32), default=None)
    result.add_argument("--svp-mask-area", type=int, default=None)
    result.add_argument("--gpu", type=int, default=None)
    result.add_argument(
        "--encoder",
        choices=SUPPORTED_ENCODER_REQUESTS,
        default="auto",
        help="auto/h264 keeps AVC compatibility; h265/hevc selects a vendor-matched HEVC encoder",
    )
    result.add_argument("--quality", type=int, default=None)
    result.add_argument(
        "--encoder-device",
        type=int,
        default=None,
        help="Optional NVENC device index supplied by the host application's GPU planner",
    )
    result.add_argument("--audio-codec", choices=("aac", "copy"), default="aac")
    result.add_argument("--audio-bitrate", default="320k")
    result.add_argument("--loglevel", default="error")
    result.add_argument("--dry-run", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        vspipe_command, ffmpeg_command, detail = build_commands(args)
        if args.dry_run:
            print(json.dumps({"vspipe": vspipe_command, "ffmpeg": ffmpeg_command, **detail}, ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(detail, ensure_ascii=False))
        ffmpeg_code, vspipe_code = run_pipeline(vspipe_command, ffmpeg_command)
        if ffmpeg_code == 0 and vspipe_code == 0:
            return 0

        fallback = detail.get("encoder_fallback")
        selected = str(detail.get("encoder") or "")
        if ffmpeg_code != 0 and isinstance(fallback, str) and fallback and fallback != selected:
            print(
                f"framemeld: hardware export failed with {selected}; retrying with {fallback}",
                file=sys.stderr,
            )
            args.output.resolve().unlink(missing_ok=True)
            fallback_vspipe, fallback_ffmpeg, fallback_detail = build_commands(
                args,
                encoder_override=fallback,
            )
            fallback_detail["fallback_from"] = selected
            print(json.dumps(fallback_detail, ensure_ascii=False))
            ffmpeg_code, vspipe_code = run_pipeline(fallback_vspipe, fallback_ffmpeg)
        return ffmpeg_code if ffmpeg_code != 0 else vspipe_code
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"framemeld: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
