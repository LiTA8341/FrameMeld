"""Run the pinned Blur VapourSynth graph and encode it with the full FFmpeg build."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from encoder_selection import (
    SUPPORTED_ENCODER_REQUESTS,
    available_encoders,
    encoder_args,
    is_hardware_encoder,
    select_encoder,
)
from engine_defaults import apply_performance_policy, merge_settings


STATUS_PREFIX = "framemeld-status:"
STATUS_PROTOCOL = "org.framemeld.status"
STATUS_VERSION = 1
_MAX_DIAGNOSTIC_BYTES = 256 * 1024
_VSPIPE_PROGRESS_RE = re.compile(rb"Frame:\s*(\d+)\s*/\s*(\d+)")
_VSPIPE_OUTPUT_RE = re.compile(rb"Output\s+(\d+)\s+frames\b", re.IGNORECASE)
_FFMPEG_PROGRESS_PAIR_RE = re.compile(rb"(?:^|[\r\n])([a-z_]+)=([^\r\n]*)")
_VULKAN_GPU_RE = re.compile(
    r"\]\s*(\d+):\s+(.+?)\s+\(([^()]*)\)\s+\(0x([0-9a-fA-F]+)\)\s*$",
)
_STDERR_LOCK = threading.Lock()
_ENGINE_ERROR_MARKERS = (
    "vapoursynth.error",
    "failed to load model",
    "rife:",
    "bestsource:",
    "failed to retrieve frame",
)
_NON_ENCODER_FFMPEG_MARKERS = (
    "invalid data found when processing input",
    "moov atom not found",
    "error opening input",
    "pipe:0: invalid data",
)


class PipelineResult:
    def __init__(
        self,
        ffmpeg_code: int,
        vspipe_code: int,
        *,
        ffmpeg_stderr: str = "",
        vspipe_stderr: str = "",
        vspipe_terminated: bool = False,
        processed_frames: int = 0,
        total_frames: int = 0,
        elapsed_ms: int = 0,
        first_frame_ms: int | None = None,
        first_packet_ms: int | None = None,
        output_bytes: int = 0,
    ) -> None:
        self.ffmpeg_code = int(ffmpeg_code)
        self.vspipe_code = int(vspipe_code)
        self.ffmpeg_stderr = str(ffmpeg_stderr or "")
        self.vspipe_stderr = str(vspipe_stderr or "")
        self.vspipe_terminated = bool(vspipe_terminated)
        self.processed_frames = max(0, int(processed_frames))
        self.total_frames = max(0, int(total_frames))
        self.elapsed_ms = max(0, int(elapsed_ms))
        self.first_frame_ms = None if first_frame_ms is None else max(0, int(first_frame_ms))
        self.first_packet_ms = None if first_packet_ms is None else max(0, int(first_packet_ms))
        self.output_bytes = max(0, int(output_bytes))

    @property
    def exit_code(self) -> int:
        return self.ffmpeg_code if self.ffmpeg_code != 0 else self.vspipe_code


class EncoderPreflightFailure(RuntimeError):
    def __init__(self, encoder: str, detail: str) -> None:
        self.encoder = str(encoder)
        self.detail = str(detail or "encoder preflight failed")
        super().__init__(f"{self.encoder}: {self.detail}")


def parse_host_encoder_adapter(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    raw_adapter = json.loads(value)
    if not isinstance(raw_adapter, dict):
        raise ValueError("Host encoder adapter metadata must be a JSON object")
    return {
        str(key): item
        for key, item in raw_adapter.items()
        if isinstance(key, str) and isinstance(item, (str, int, float, bool))
    }


def encoder_device_status(
    encoder: str,
    encoder_device: int | None,
    host_encoder_adapter: dict[str, Any] | None = None,
    *,
    preflight: bool = False,
) -> dict[str, Any]:
    """Describe encoder selection without claiming an unverified GPU binding."""

    normalized = str(encoder or "").casefold()
    if normalized.endswith("_nvenc"):
        vendor = "nvidia"
        explicit_binding_supported = True
    elif normalized.endswith("_amf"):
        vendor = "amd"
        explicit_binding_supported = False
    elif normalized.endswith("_qsv"):
        # QSV is an independent Intel policy branch.  The current FFmpeg
        # command does not bind a DXGI adapter explicitly, so report the host
        # plan separately from the runtime's system-default selection.
        vendor = "intel"
        explicit_binding_supported = False
    elif normalized.startswith("libx"):
        vendor = "cpu"
        explicit_binding_supported = False
    else:
        vendor = "unknown"
        explicit_binding_supported = False

    explicit = bool(encoder_device is not None and explicit_binding_supported)
    return {
        "backend": encoder,
        "vendor": vendor,
        "index": encoder_device if explicit else None,
        "selection": "preflight" if preflight else ("explicit" if explicit else "system-default"),
        "explicit_binding_supported": explicit_binding_supported,
        "host_planned_adapter": host_encoder_adapter or None,
    }


def _gpu_vendor_from_name(value: str) -> str:
    normalized = str(value or "").casefold()
    if "nvidia" in normalized:
        return "nvidia"
    if "amd" in normalized or "radeon" in normalized or "advanced micro devices" in normalized:
        return "amd"
    if "intel" in normalized:
        return "intel"
    return "unknown"


def _normalized_gpu_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def probe_vulkan_inventory(ffmpeg: Path) -> dict[str, Any]:
    """Ask FFmpeg to list Vulkan devices without initializing a real export.

    FFmpeg 9 currently exposes an index, name, type and PCI device ID here,
    but not the Vulkan UUID or Windows LUID.  Missing stable identifiers remain
    explicit so callers cannot mistake this inventory for an exact mapping.
    """

    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "verbose",
        "-init_hw_device",
        "vulkan=framemeld_inventory:2147483647",
        "-f",
        "lavfi",
        "-i",
        "nullsrc=s=16x16:d=0.01",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        devices: list[dict[str, Any]] = []
        seen: set[int] = set()
        for line in output.replace("\r", "\n").splitlines():
            match = _VULKAN_GPU_RE.search(line)
            if match is None:
                continue
            index = int(match.group(1))
            if index in seen:
                continue
            seen.add(index)
            name = match.group(2).strip()
            devices.append(
                {
                    "index": index,
                    "name": name,
                    "vendor": _gpu_vendor_from_name(name),
                    "device_type": match.group(3).strip().casefold(),
                    "device_id": match.group(4).upper().zfill(4),
                    "uuid": None,
                    "luid": None,
                    "stable_identity_available": False,
                    "identity_source": "ffmpeg_vulkan_listing",
                }
            )
        return {
            "status": "succeeded" if devices else "unavailable",
            "probe_returncode": int(result.returncode),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "devices": devices,
            "stable_identity_fields": {
                "uuid": "not_exposed_by_ffmpeg_listing",
                "luid": "not_exposed_by_ffmpeg_listing",
            },
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "failed",
            "probe_returncode": None,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "devices": [],
            "stable_identity_fields": {"uuid": "unknown", "luid": "unknown"},
            "error": f"{type(exc).__name__}: {exc}",
        }


def map_host_adapter_to_vulkan(
    host_adapter: dict[str, Any] | None,
    vulkan_inventory: dict[str, Any],
    rife_index: int,
) -> dict[str, Any]:
    """Return a conservative DXGI-to-Vulkan candidate mapping.

    The RIFE/ncnn index space is deliberately not declared equal to FFmpeg's
    Vulkan index space.  This event gathers enough evidence to establish that
    relationship on affected machines in a later runtime revision.
    """

    raw_devices = vulkan_inventory.get("devices")
    devices = [item for item in raw_devices if isinstance(item, dict)] if isinstance(raw_devices, list) else []
    selected = next((item for item in devices if item.get("index") == rife_index), None)
    planned = dict(host_adapter or {})
    planned_vendor = str(planned.get("vendor") or "unknown").casefold()
    raw_planned_device_id = re.sub(
        r"^0x",
        "",
        str(planned.get("device_id") or "").strip(),
        flags=re.IGNORECASE,
    ).upper()
    planned_device_id = raw_planned_device_id.zfill(4) if raw_planned_device_id else ""
    planned_name = _normalized_gpu_name(planned.get("name"))

    candidates = []
    for device in devices:
        same_vendor = planned_vendor != "unknown" and device.get("vendor") == planned_vendor
        same_device = bool(planned_device_id) and str(device.get("device_id") or "").upper() == planned_device_id
        same_name = bool(planned_name) and _normalized_gpu_name(device.get("name")) == planned_name
        if same_vendor and (same_device or same_name):
            candidates.append(device)

    if not devices:
        outcome, confidence, reason = "unknown", "none", "vulkan_inventory_unavailable"
    elif not planned:
        outcome, confidence, reason = "unknown", "none", "host_planned_adapter_missing"
    elif selected is None:
        outcome, confidence, reason = "unmatched", "none", "rife_index_not_present_in_ffmpeg_vulkan_listing"
    elif selected not in candidates:
        outcome, confidence, reason = "unmatched", "none", "selected_vulkan_device_differs_from_host_adapter"
    elif len(candidates) > 1:
        outcome, confidence, reason = "ambiguous", "low", "multiple_same_identity_candidates_without_uuid_or_luid"
    elif planned_device_id and selected.get("device_id") == planned_device_id:
        outcome, confidence, reason = "candidate", "medium", "unique_vendor_and_device_id_match_without_stable_identity"
    else:
        outcome, confidence, reason = "candidate", "low", "unique_vendor_and_name_match_without_stable_identity"

    return {
        "scope": "host_dxgi_to_rife_vulkan_candidate",
        "outcome": outcome,
        "confidence": confidence,
        "reason": reason,
        "host_planned_adapter": planned or None,
        "rife_requested_index": int(rife_index),
        "selected_ffmpeg_vulkan_device": selected,
        "candidate_vulkan_indices": [item.get("index") for item in candidates],
        "index_spaces": {
            "rife": "ncnn_vulkan",
            "inventory": "ffmpeg_vulkan",
            "verified_equal": False,
        },
        "exact_mapping_available": False,
    }


def emit_status(enabled: bool, event: str, **payload: Any) -> None:
    if not enabled:
        return
    record = {
        "protocol": STATUS_PROTOCOL,
        "version": STATUS_VERSION,
        "event": str(event),
        **payload,
    }
    with _STDERR_LOCK:
        print(
            STATUS_PREFIX + json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            file=sys.stderr,
            flush=True,
        )


def _diagnostic_tail(buffer: bytearray) -> str:
    return bytes(buffer).decode("utf-8", errors="replace")[-12000:]


def _append_diagnostic(buffer: bytearray, chunk: bytes) -> None:
    buffer.extend(chunk)
    overflow = len(buffer) - _MAX_DIAGNOSTIC_BYTES
    if overflow > 0:
        del buffer[:overflow]


def _forward_stderr(chunk: bytes) -> None:
    with _STDERR_LOCK:
        binary = getattr(sys.stderr, "buffer", None)
        if binary is not None:
            binary.write(chunk)
            binary.flush()
        else:
            sys.stderr.write(chunk.decode("utf-8", errors="replace"))
            sys.stderr.flush()


def failure_domain(result: PipelineResult, encoder: str) -> str:
    engine_text = result.vspipe_stderr.casefold()
    if result.vspipe_code != 0 and any(marker in engine_text for marker in _ENGINE_ERROR_MARKERS):
        return "frame_engine"
    if result.ffmpeg_code == 0 and result.vspipe_code != 0:
        return "frame_engine"
    if result.ffmpeg_code != 0:
        ffmpeg_text = result.ffmpeg_stderr.casefold()
        if any(marker in ffmpeg_text for marker in _NON_ENCODER_FFMPEG_MARKERS):
            return "ffmpeg_pipeline"
        codec = str(encoder or "").strip().casefold()
        codec_reported_error = bool(codec) and codec in ffmpeg_text and any(
            marker in ffmpeg_text for marker in ("error", "failed", "cannot", "unsupported")
        )
        if codec_reported_error:
            return "encoder"
        return "ffmpeg_pipeline"
    return "unknown"


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
    # RIFE's Windows model preflight uses a narrow-character path.  Supplying
    # the pinned ASCII-only path relative to the VapourSynth script directory
    # avoids leaking a non-ASCII installation directory into that plugin.
    settings = merge_settings(
        overrides,
        model.relative_to(engine_script.parent),
        resolve_model_path=False,
    )
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
        requested_encoder = str(args.encoder or "").strip().casefold()
        if (
            args.host_managed_encoder_fallback
            and is_hardware_encoder(requested_encoder)
            and encoder != requested_encoder
        ):
            requested_attempt = next(
                (
                    attempt
                    for attempt in encoder_plan.attempts
                    if attempt.encoder == requested_encoder
                ),
                None,
            )
            raise EncoderPreflightFailure(
                requested_encoder,
                requested_attempt.detail if requested_attempt is not None else "requested encoder was not selected",
            )
    else:
        if encoder_override not in available_encoders(ffmpeg):
            raise RuntimeError(f"Fallback encoder is unavailable: {encoder_override}")
        encoder = encoder_override
        encoder_fallback = None
        gpu_vendors = []
        encoder_attempts = []

    host_encoder_adapter = parse_host_encoder_adapter(args.host_encoder_adapter_json)
    vulkan_inventory = (
        probe_vulkan_inventory(ffmpeg)
        if args.status_json_lines
        else {
            "status": "not_requested",
            "probe_returncode": None,
            "elapsed_ms": 0,
            "devices": [],
            "stable_identity_fields": {"uuid": "unknown", "luid": "unknown"},
        }
    )

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

    ffmpeg_progress_args = (
        ["-nostats", "-stats_period", "5", "-progress", "pipe:2"]
        if args.status_json_lines
        else ["-stats"]
    )
    ffmpeg_command = [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", args.loglevel, *ffmpeg_progress_args,
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
    encoder_device_applied = args.encoder_device is not None and encoder.endswith("_nvenc")
    rife_index = int(settings["rife_gpu_index"])
    device_mapping = map_host_adapter_to_vulkan(
        host_encoder_adapter,
        vulkan_inventory,
        rife_index,
    )
    return vspipe_command, ffmpeg_command, {
        "engine": ",".join(active_engines) or "passthrough",
        "encoder": encoder,
        "encoder_fallback": encoder_fallback,
        "encoder_device": args.encoder_device if encoder_device_applied else None,
        "devices": {
            "rife": {
                "index": rife_index,
                "selection": "explicit" if args.gpu is not None else "default",
            },
            "encoder": encoder_device_status(
                encoder,
                args.encoder_device if encoder_device_applied else None,
                host_encoder_adapter,
            ),
        },
        "vulkan_inventory": vulkan_inventory,
        "device_mapping": device_mapping,
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


def run_pipeline(
    vspipe_command: list[str],
    ffmpeg_command: list[str],
    *,
    status_json_lines: bool = False,
) -> PipelineResult:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    vspipe = subprocess.Popen(
        vspipe_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
        cwd=runtime_root() / "lib",
    )
    assert vspipe.stdout is not None
    ffmpeg = subprocess.Popen(
        ffmpeg_command,
        stdin=vspipe.stdout,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    vspipe.stdout.close()
    assert vspipe.stderr is not None and ffmpeg.stderr is not None
    vspipe_stderr = bytearray()
    ffmpeg_stderr = bytearray()
    started = time.monotonic()
    progress_state = {
        "processed": 0,
        "total": 0,
        "rolling": b"",
        "ffmpeg_rolling": b"",
        "first_frame_ms": None,
        "first_packet_ms": None,
        "output_bytes": 0,
        "status_progress_bucket": -1,
        "status_progress_at": started,
    }

    def drain_stderr(
        stream: Any,
        target: bytearray,
        *,
        parse_progress: bool = False,
        parse_ffmpeg_progress: bool = False,
    ) -> None:
        try:
            read_chunk = getattr(stream, "read1", stream.read)
            while True:
                chunk = read_chunk(65536)
                if not chunk:
                    return
                _append_diagnostic(target, chunk)
                _forward_stderr(chunk)
                if parse_progress:
                    rolling = (progress_state["rolling"] + chunk)[-8192:]
                    progress_state["rolling"] = rolling[-256:]
                    matches = list(_VSPIPE_PROGRESS_RE.finditer(rolling))
                    if matches:
                        processed = int(matches[-1].group(1))
                        total = int(matches[-1].group(2))
                        if total > 0 and processed > int(progress_state["processed"]):
                            progress_state["processed"] = processed
                            progress_state["total"] = total
                            elapsed_ms = round((time.monotonic() - started) * 1000)
                            if progress_state["first_frame_ms"] is None:
                                progress_state["first_frame_ms"] = elapsed_ms
                                emit_status(
                                    status_json_lines,
                                    "first_frame",
                                    status="observed",
                                    stage="frame_engine",
                                    processed_frames=processed,
                                    elapsed_ms=elapsed_ms,
                                    measurement="first_vspipe_progress_observation",
                                    upper_bound=True,
                                )
                            progress_value = min(1.0, processed / total)
                            progress_bucket = min(100, int(progress_value * 100))
                            progress_now = time.monotonic()
                            if (
                                progress_bucket > int(progress_state["status_progress_bucket"])
                                or progress_now - float(progress_state["status_progress_at"]) >= 10.0
                                or processed >= total
                            ):
                                progress_state["status_progress_bucket"] = progress_bucket
                                progress_state["status_progress_at"] = progress_now
                                emit_status(
                                    status_json_lines,
                                    "progress",
                                    status="running",
                                    stage="frame_engine",
                                    processed_frames=processed,
                                    total_frames=total,
                                    progress=progress_value,
                                    elapsed_ms=elapsed_ms,
                                )
                if parse_ffmpeg_progress:
                    ffmpeg_rolling = (progress_state["ffmpeg_rolling"] + chunk)[-8192:]
                    progress_state["ffmpeg_rolling"] = ffmpeg_rolling[-512:]
                    for match in _FFMPEG_PROGRESS_PAIR_RE.finditer(ffmpeg_rolling):
                        if match.group(1) != b"total_size":
                            continue
                        try:
                            output_bytes = int(match.group(2).strip())
                        except ValueError:
                            continue
                        progress_state["output_bytes"] = max(
                            int(progress_state["output_bytes"]),
                            output_bytes,
                        )
                        if output_bytes <= 0 or progress_state["first_packet_ms"] is not None:
                            continue
                        elapsed_ms = round((time.monotonic() - started) * 1000)
                        progress_state["first_packet_ms"] = elapsed_ms
                        emit_status(
                            status_json_lines,
                            "first_packet",
                            status="observed",
                            stage="encoder_muxer",
                            output_bytes=output_bytes,
                            elapsed_ms=elapsed_ms,
                            measurement="ffmpeg_progress_first_positive_total_size",
                            upper_bound=True,
                        )
        except (OSError, ValueError):
            return

    readers = [
        threading.Thread(
            target=drain_stderr,
            args=(vspipe.stderr, vspipe_stderr),
            kwargs={"parse_progress": True},
            daemon=True,
        ),
        threading.Thread(
            target=drain_stderr,
            args=(ffmpeg.stderr, ffmpeg_stderr),
            kwargs={"parse_ffmpeg_progress": status_json_lines},
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    vspipe_terminated = False
    try:
        ffmpeg_code = ffmpeg.wait()
    except KeyboardInterrupt:
        ffmpeg.terminate()
        vspipe.terminate()
        raise
    if ffmpeg_code != 0 and vspipe.poll() is None:
        vspipe_terminated = True
        vspipe.terminate()
    vspipe_code = vspipe.wait()
    for reader in readers:
        reader.join(timeout=5.0)
    output_matches = list(_VSPIPE_OUTPUT_RE.finditer(bytes(vspipe_stderr)))
    if output_matches:
        final_frames = int(output_matches[-1].group(1))
        progress_state["processed"] = max(int(progress_state["processed"]), final_frames)
        progress_state["total"] = max(int(progress_state["total"]), final_frames)
    return PipelineResult(
        ffmpeg_code,
        vspipe_code,
        ffmpeg_stderr=_diagnostic_tail(ffmpeg_stderr),
        vspipe_stderr=_diagnostic_tail(vspipe_stderr),
        vspipe_terminated=vspipe_terminated,
        processed_frames=int(progress_state["processed"]),
        total_frames=int(progress_state["total"]),
        elapsed_ms=round((time.monotonic() - started) * 1000),
        first_frame_ms=progress_state["first_frame_ms"],
        first_packet_ms=progress_state["first_packet_ms"],
        output_bytes=int(progress_state["output_bytes"]),
    )


def emit_pre_pipeline_diagnostics(enabled: bool, detail: dict[str, Any]) -> None:
    inventory = detail.get("vulkan_inventory")
    if isinstance(inventory, dict):
        emit_status(
            enabled,
            "device_inventory",
            status=inventory.get("status"),
            source="framemeld_ffmpeg_vulkan",
            probe_returncode=inventory.get("probe_returncode"),
            elapsed_ms=inventory.get("elapsed_ms"),
            devices=inventory.get("devices") or [],
            stable_identity_fields=inventory.get("stable_identity_fields") or {},
            error=inventory.get("error"),
        )
    mapping = detail.get("device_mapping")
    if isinstance(mapping, dict):
        emit_status(enabled, "device_mapping", status=mapping.get("outcome"), **mapping)


def emit_post_pipeline_diagnostics(
    enabled: bool,
    detail: dict[str, Any],
    result: PipelineResult,
) -> None:
    devices = detail.get("devices") if isinstance(detail.get("devices"), dict) else {}
    encoder_device = devices.get("encoder") if isinstance(devices, dict) else {}
    encoder_device = encoder_device if isinstance(encoder_device, dict) else {}
    requested_index = encoder_device.get("index")
    explicit_requested = encoder_device.get("selection") == "explicit"
    if explicit_requested:
        binding_state = "requested_index_only_unverified"
    elif encoder_device.get("vendor") in {"amd", "intel"}:
        binding_state = "system_default_unverified"
    elif encoder_device.get("vendor") == "cpu":
        binding_state = "not_applicable_cpu"
    else:
        binding_state = "unverified"
    if result.ffmpeg_code == 0:
        initialization_result = "succeeded"
    elif result.first_packet_ms is not None:
        initialization_result = "started_then_failed"
    else:
        initialization_result = "failed_or_not_observed"
    emit_status(
        enabled,
        "encoder_binding",
        status=initialization_result,
        requested={
            "backend": detail.get("encoder"),
            "adapter": encoder_device.get("host_planned_adapter"),
            "device_index": requested_index,
            "selection": encoder_device.get("selection"),
        },
        actual={
            "backend": detail.get("encoder"),
            "adapter": None,
            "device_index": None,
            "stable_identity": None,
        },
        binding_state=binding_state,
        binding_verified=False,
        initialization_result=initialization_result,
        initialization_evidence=(
            "first_output_packet_observed"
            if result.first_packet_ms is not None
            else "ffmpeg_process_returncode"
        ),
        ffmpeg_returncode=result.ffmpeg_code,
        first_packet_ms=result.first_packet_ms,
    )
    effective_fps = (
        round(result.processed_frames * 1000 / result.elapsed_ms, 3)
        if result.processed_frames > 0 and result.elapsed_ms > 0
        else None
    )
    emit_status(
        enabled,
        "performance_summary",
        status="succeeded" if result.exit_code == 0 else "failed",
        encoder=detail.get("encoder"),
        processed_frames=result.processed_frames,
        total_frames=result.total_frames,
        elapsed_ms=result.elapsed_ms,
        effective_pipeline_fps=effective_fps,
        first_frame_ms=result.first_frame_ms,
        first_frame_observed=result.first_frame_ms is not None,
        first_packet_ms=result.first_packet_ms,
        first_packet_observed=result.first_packet_ms is not None,
        output_bytes_observed=result.output_bytes,
        ffmpeg_returncode=result.ffmpeg_code,
        vspipe_returncode=result.vspipe_code,
    )


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
    result.add_argument(
        "--host-encoder-adapter-json",
        default=None,
        help="Optional host-planned adapter metadata echoed in structured status events",
    )
    result.add_argument("--audio-codec", choices=("aac", "copy"), default="aac")
    result.add_argument("--audio-bitrate", default="320k")
    result.add_argument("--loglevel", default="error")
    result.add_argument(
        "--host-managed-encoder-fallback",
        action="store_true",
        help="Return the requested encoder failure to the host instead of retrying in FrameMeld",
    )
    result.add_argument(
        "--status-json-lines",
        action="store_true",
        help="Emit prefixed machine-readable lifecycle, device, and failure events on stderr",
    )
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
        emit_pre_pipeline_diagnostics(args.status_json_lines, detail)
        emit_status(
            args.status_json_lines,
            "pipeline_started",
            status="running",
            encoder=detail.get("encoder"),
            devices=detail.get("devices"),
            engine=detail.get("engine"),
            interpolation=detail.get("interpolation"),
        )
        result = run_pipeline(
            vspipe_command,
            ffmpeg_command,
            status_json_lines=args.status_json_lines,
        )
        emit_post_pipeline_diagnostics(args.status_json_lines, detail, result)
        if result.exit_code == 0:
            emit_status(
                args.status_json_lines,
                "pipeline_finished",
                status="succeeded",
                encoder=detail.get("encoder"),
                devices=detail.get("devices"),
                ffmpeg_returncode=result.ffmpeg_code,
                vspipe_returncode=result.vspipe_code,
                processed_frames=result.processed_frames,
                total_frames=result.total_frames,
                elapsed_ms=result.elapsed_ms,
            )
            return 0

        fallback = detail.get("encoder_fallback")
        selected = str(detail.get("encoder") or "")
        domain = failure_domain(result, selected)
        emit_status(
            args.status_json_lines,
            "pipeline_finished",
            status="failed",
            failure_domain=domain,
            encoder=selected,
            devices=detail.get("devices"),
            ffmpeg_returncode=result.ffmpeg_code,
            vspipe_returncode=result.vspipe_code,
            processed_frames=result.processed_frames,
            total_frames=result.total_frames,
            elapsed_ms=result.elapsed_ms,
            ffmpeg_stderr_tail=result.ffmpeg_stderr[-2000:],
            vspipe_stderr_tail=result.vspipe_stderr[-2000:],
        )
        if (
            domain == "encoder"
            and not args.host_managed_encoder_fallback
            and isinstance(fallback, str)
            and fallback
            and fallback != selected
        ):
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
            emit_pre_pipeline_diagnostics(args.status_json_lines, fallback_detail)
            emit_status(
                args.status_json_lines,
                "pipeline_started",
                status="running",
                encoder=fallback_detail.get("encoder"),
                devices=fallback_detail.get("devices"),
                fallback_from=selected,
            )
            result = run_pipeline(
                fallback_vspipe,
                fallback_ffmpeg,
                status_json_lines=args.status_json_lines,
            )
            emit_post_pipeline_diagnostics(args.status_json_lines, fallback_detail, result)
            fallback_domain = None if result.exit_code == 0 else failure_domain(
                result,
                str(fallback_detail.get("encoder") or ""),
            )
            emit_status(
                args.status_json_lines,
                "pipeline_finished",
                status="succeeded" if result.exit_code == 0 else "failed",
                failure_domain=fallback_domain,
                encoder=fallback_detail.get("encoder"),
                devices=fallback_detail.get("devices"),
                fallback_from=selected,
                ffmpeg_returncode=result.ffmpeg_code,
                vspipe_returncode=result.vspipe_code,
                processed_frames=result.processed_frames,
                total_frames=result.total_frames,
                elapsed_ms=result.elapsed_ms,
                ffmpeg_stderr_tail=result.ffmpeg_stderr[-2000:] if result.exit_code else "",
                vspipe_stderr_tail=result.vspipe_stderr[-2000:] if result.exit_code else "",
            )
        elif result.ffmpeg_code != 0 and args.host_managed_encoder_fallback:
            print(
                f"framemeld: pipeline failed with {selected} domain={domain}; returning control to host",
                file=sys.stderr,
            )
        return result.exit_code
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, EncoderPreflightFailure):
            domain = "encoder"
        elif isinstance(exc, FileNotFoundError):
            domain = "runtime_or_input"
        elif isinstance(exc, (ValueError, json.JSONDecodeError)):
            domain = "configuration"
        else:
            domain = "preflight"
        try:
            host_adapter = parse_host_encoder_adapter(args.host_encoder_adapter_json)
        except (ValueError, json.JSONDecodeError):
            host_adapter = {}
        failed_encoder = exc.encoder if isinstance(exc, EncoderPreflightFailure) else ""
        emit_status(
            args.status_json_lines,
            "encoder_binding",
            status="preflight_failed",
            requested={
                "backend": failed_encoder,
                "adapter": host_adapter or None,
                "device_index": None,
                "selection": "preflight",
            },
            actual={
                "backend": None,
                "adapter": None,
                "device_index": None,
                "stable_identity": None,
            },
            binding_state="not_initialized",
            binding_verified=False,
            initialization_result="preflight_failed",
            detail=str(exc),
        )
        emit_status(
            args.status_json_lines,
            "startup_failed",
            status="failed",
            failure_domain=domain,
            encoder=failed_encoder,
            devices={
                "rife": {
                    "index": max(0, int(args.gpu or 0)),
                    "selection": "explicit" if args.gpu is not None else "default",
                },
                "encoder": encoder_device_status(
                    failed_encoder,
                    None,
                    host_adapter,
                    preflight=True,
                ),
            },
            detail=str(exc),
        )
        print(f"framemeld: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
