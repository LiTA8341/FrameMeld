"""Cross-vendor H.264/HEVC encoder discovery, probing, and fallback planning."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


GpuVendor = str
EncoderProbe = Callable[[Path, str], tuple[bool, str]]

_VENDOR_PRIORITY: tuple[GpuVendor, ...] = ("nvidia", "amd", "intel")
_VENDOR_ENCODERS: dict[str, dict[GpuVendor, str]] = {
    "h264": {
        "nvidia": "h264_nvenc",
        "amd": "h264_amf",
        "intel": "h264_qsv",
    },
    "hevc": {
        "nvidia": "hevc_nvenc",
        "amd": "hevc_amf",
        "intel": "hevc_qsv",
    },
}
_SOFTWARE_ENCODERS = {"h264": "libx264", "hevc": "libx265"}
_ENCODER_FAMILY = {
    encoder: family
    for family, encoders in _VENDOR_ENCODERS.items()
    for encoder in encoders.values()
} | {encoder: family for family, encoder in _SOFTWARE_ENCODERS.items()}
_FAMILY_ALIASES = {
    "h264": "h264",
    "avc": "h264",
    "h265": "hevc",
    "hevc": "hevc",
}
SUPPORTED_ENCODER_REQUESTS = (
    "auto",
    "h264",
    "h265",
    "hevc",
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
    "libx264",
    "hevc_nvenc",
    "hevc_qsv",
    "hevc_amf",
    "libx265",
)


@dataclass(frozen=True)
class EncoderAttempt:
    encoder: str
    compiled: bool
    usable: bool
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "encoder": self.encoder,
            "compiled": self.compiled,
            "usable": self.usable,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class EncoderPlan:
    family: str
    selected: str
    fallback: str | None
    gpu_vendors: tuple[GpuVendor, ...]
    candidates: tuple[str, ...]
    attempts: tuple[EncoderAttempt, ...]


def available_encoders(ffmpeg: Path) -> set[str]:
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return set()
    return set(re.findall(r"^\s*[A-Z.]{6}\s+(\S+)", result.stdout, re.MULTILINE))


def _gpu_vendor(name: str) -> GpuVendor | None:
    normalized = name.casefold()
    if "nvidia" in normalized:
        return "nvidia"
    if any(token in normalized for token in ("advanced micro devices", "amd", "radeon")):
        return "amd"
    if "intel" in normalized:
        return "intel"
    return None


def detect_gpu_vendors() -> tuple[GpuVendor, ...]:
    """Return installed Windows GPU vendors in preferred discrete-first order."""

    if os.name != "nt":
        return ()
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name | ConvertTo-Json -Compress",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ()
        payload = json.loads(result.stdout)
        names = payload if isinstance(payload, list) else [payload]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return ()
    found = {_gpu_vendor(str(name)) for name in names}
    return tuple(vendor for vendor in _VENDOR_PRIORITY if vendor in found)


def encoder_family(requested: str) -> str:
    normalized = str(requested or "auto").strip().casefold()
    if normalized == "auto":
        return "h264"
    if normalized in _FAMILY_ALIASES:
        return _FAMILY_ALIASES[normalized]
    family = _ENCODER_FAMILY.get(normalized)
    if family:
        return family
    raise ValueError(f"Unsupported encoder: {requested}")


def is_hardware_encoder(encoder: str) -> bool:
    return encoder in _ENCODER_FAMILY and encoder not in _SOFTWARE_ENCODERS.values()


def build_encoder_candidates(requested: str, gpu_vendors: Iterable[GpuVendor]) -> tuple[str, ...]:
    """Build vendor-matched candidates followed by a same-family CPU safeguard."""

    normalized = str(requested or "auto").strip().casefold()
    family = encoder_family(normalized)
    software = _SOFTWARE_ENCODERS[family]
    if normalized in _ENCODER_FAMILY:
        if normalized == software:
            return (software,)
        return (normalized, software)

    vendors = tuple(dict.fromkeys(vendor for vendor in gpu_vendors if vendor in _VENDOR_PRIORITY))
    if not vendors:
        vendors = _VENDOR_PRIORITY
    hardware = tuple(_VENDOR_ENCODERS[family][vendor] for vendor in vendors)
    return (*hardware, software)


def encoder_args(encoder: str, quality: int) -> list[str]:
    q = max(0, min(51, int(quality)))
    if encoder in {"h264_nvenc", "hevc_nvenc"}:
        args = ["-c:v", encoder, "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", str(q), "-b:v", "0"]
        # FFmpeg 9.0 changed h264_nvenc's default profile from Main to High.
        # Pin Main to preserve the bitstream compatibility of existing outputs.
        if encoder == "h264_nvenc":
            args.extend(["-profile:v", "main"])
        return args
    if encoder in {"h264_qsv", "hevc_qsv"}:
        return ["-c:v", encoder, "-preset", "slow", "-global_quality", str(max(1, q))]
    if encoder in {"h264_amf", "hevc_amf"}:
        return [
            "-c:v",
            encoder,
            "-quality",
            "quality",
            "-rc",
            "cqp",
            "-qp_i",
            str(q),
            "-qp_p",
            str(q),
        ]
    if encoder == "libx264":
        return ["-c:v", encoder, "-preset", "medium", "-crf", str(q)]
    if encoder == "libx265":
        return ["-c:v", encoder, "-preset", "medium", "-crf", str(q)]
    raise ValueError(f"Unsupported encoder: {encoder}")


def probe_encoder(ffmpeg: Path, encoder: str) -> tuple[bool, str]:
    """Open the real encoder with a short synthetic workload."""

    amf = encoder.endswith("_amf")
    source = "testsrc2=s=1920x1080:r=60:d=1,format=yuv420p" if amf else "testsrc2=s=320x240:r=30:d=0.1,format=yuv420p"
    frame_count = "60" if amf else "3"
    command = [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        source,
        "-frames:v",
        frame_count,
        "-an",
        *encoder_args(encoder, 28),
        "-pix_fmt",
        "yuv420p",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    detail = (result.stderr or result.stdout or "").strip()[-1200:]
    return result.returncode == 0, detail


def select_encoder(
    ffmpeg: Path,
    requested: str,
    *,
    gpu_vendors: Iterable[GpuVendor] | None = None,
    compiled_encoders: Iterable[str] | None = None,
    encoder_probe: EncoderProbe = probe_encoder,
) -> EncoderPlan:
    """Select the first encoder that passes a runtime probe and retain CPU fallback."""

    vendors = tuple(gpu_vendors) if gpu_vendors is not None else detect_gpu_vendors()
    candidates = build_encoder_candidates(requested, vendors)
    compiled = set(compiled_encoders) if compiled_encoders is not None else available_encoders(ffmpeg)
    family = encoder_family(requested)
    software = _SOFTWARE_ENCODERS[family]
    selected: str | None = None
    software_usable = False
    attempts: list[EncoderAttempt] = []

    for candidate in candidates:
        if selected is not None and candidate != software:
            continue
        if candidate not in compiled:
            attempts.append(EncoderAttempt(candidate, False, False, "not compiled into FFmpeg"))
            continue
        usable, detail = encoder_probe(ffmpeg, candidate)
        attempts.append(EncoderAttempt(candidate, True, usable, detail))
        if usable and selected is None:
            selected = candidate
        if candidate == software:
            software_usable = usable

    if selected is None:
        summary = "; ".join(
            f"{attempt.encoder}: {attempt.detail or 'runtime probe failed'}" for attempt in attempts
        )
        raise RuntimeError(f"No usable {family.upper()} encoder was found ({summary})")
    fallback = software if selected != software and software_usable else None
    return EncoderPlan(
        family=family,
        selected=selected,
        fallback=fallback,
        gpu_vendors=vendors,
        candidates=candidates,
        attempts=tuple(attempts),
    )


__all__ = [
    "EncoderAttempt",
    "EncoderPlan",
    "SUPPORTED_ENCODER_REQUESTS",
    "available_encoders",
    "build_encoder_candidates",
    "detect_gpu_vendors",
    "encoder_args",
    "encoder_family",
    "is_hardware_encoder",
    "probe_encoder",
    "select_encoder",
]
