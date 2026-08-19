"""Validated defaults for the independent headless frame engine."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from math import ceil
from pathlib import Path
from typing import Any


BUILD_FLAVOR = "fast"
POLICY_ID = "source-relative-fast-v1"


DEFAULT_SETTINGS: dict[str, Any] = {
    "blur": True,
    "blur_amount": 1.0,
    "blur_taps": 0,
    "blur_output_fps": 60,
    "blur_weighting": "equal",
    "blur_gamma": 1.0,
    "interpolate": True,
    "interpolated_fps": "240",
    "interpolation_method": "rife",
    "pre_interpolate": False,
    "pre_interpolated_fps": "120",
    "deduplicate": True,
    "deduplicate_method": "svp",
    "timescale": False,
    "input_timescale": 1.0,
    "output_timescale": 1.0,
    "output_timescale_audio_pitch": False,
    "filters": False,
    "brightness": 1.0,
    "saturation": 1.0,
    "contrast": 1.0,
    "quality": 18,
    "preview": False,
    "detailed_filenames": False,
    "gpu_decoding": False,
    "gpu_interpolation": True,
    "gpu_encoding": True,
    "performance_mode": "balanced",
    "performance_samples": 6,
    "adaptive_motion_threshold": 0.002,
    "adaptive_scene_threshold": 0.20,
    "analysis_width": 320,
    "deduplicate_range": 2,
    "deduplicate_threshold": "0.001",
    "debug": False,
    "blur_weighting_gaussian_std_dev": 1.0,
    "blur_weighting_gaussian_mean": 2.0,
    "blur_weighting_gaussian_bound": "[0,2]",
    "svp_interpolation_preset": "weak",
    "svp_interpolation_algorithm": "13",
    "interpolation_blocksize": "8",
    "interpolation_mask_area": 0,
    "manual_svp": False,
    "super_string": "",
    "vectors_string": "",
    "smooth_string": "",
    "rife_gpu_index": 0,
}

WEIGHTINGS = {
    "equal",
    "gaussian_sym",
    "vegas",
    "pyramid",
    "gaussian",
    "ascending",
    "descending",
    "gaussian_reverse",
}


# Fast keeps the public balanced/adaptive interface while lowering the RIFE
# timeline one tier.  Multipliers are applied to the exact reported rational
# rate, so NTSC-family sources keep their original timing.
VERIFIED_FRAME_RATE_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "name": "fast-60fps",
        "nominal_fps": Fraction(60, 1),
        "multiplier": 4,
        "blur_amount": 1.0,
        "blur_taps": 5,
        "status": "subjectively-confirmed",
    },
    {
        "name": "fast-90fps",
        "nominal_fps": Fraction(90, 1),
        "multiplier": 3,
        "blur_amount": 1.0,
        "blur_taps": 0,
        "status": "candidate",
    },
    {
        "name": "fast-120fps",
        "nominal_fps": Fraction(120, 1),
        "multiplier": 2,
        "blur_amount": 0.85,
        "blur_taps": 0,
        "status": "candidate",
    },
    {
        "name": "fast-144fps",
        "nominal_fps": Fraction(144, 1),
        "multiplier": 2,
        "blur_amount": 0.925,
        "blur_taps": 0,
        "status": "candidate",
    },
    {
        "name": "fast-180fps",
        "nominal_fps": Fraction(180, 1),
        "multiplier": 2,
        "blur_amount": 0.925,
        "blur_taps": 0,
        "status": "existing-quality-profile",
    },
    {
        "name": "fast-240fps",
        "nominal_fps": Fraction(240, 1),
        "multiplier": 1,
        "blur_amount": 1.0,
        "blur_taps": 0,
        "status": "candidate",
    },
    {
        "name": "fast-360fps",
        "nominal_fps": Fraction(360, 1),
        "multiplier": 1,
        "blur_amount": 1.0,
        "blur_taps": 7,
        "status": "existing-quality-profile",
    },
)


def verified_frame_rate_profile(source_fps: Fraction | None) -> dict[str, Any] | None:
    """Return the approved profile when the reported rate is within 0.5fps."""

    if source_fps is None:
        return None
    tolerance = Fraction(1, 2)
    for profile in VERIFIED_FRAME_RATE_PROFILES:
        if abs(source_fps - profile["nominal_fps"]) <= tolerance:
            return profile
    return None


def merge_settings(
    overrides: dict[str, Any] | None,
    model_path: Path,
    *,
    resolve_model_path: bool = True,
) -> dict[str, Any]:
    settings = deepcopy(DEFAULT_SETTINGS)
    if overrides:
        unknown = sorted(set(overrides) - set(settings) - {"rife_model"})
        if unknown:
            raise ValueError(f"Unknown Blur setting(s): {', '.join(unknown)}")
        settings.update(overrides)
    settings["rife_model"] = str(model_path.resolve() if resolve_model_path else model_path)
    validate_settings(settings)
    return settings


def validate_settings(settings: dict[str, Any]) -> None:
    if settings["interpolation_method"] not in {"rife", "svp"}:
        raise ValueError("interpolation_method must be 'rife' or 'svp'")
    if settings["deduplicate_method"] not in {"rife", "svp", "old"}:
        raise ValueError("deduplicate_method must be 'rife', 'svp', or 'old'")
    if settings["performance_mode"] not in {"original", "exact", "balanced", "adaptive"}:
        raise ValueError(
            "performance_mode must be 'original', 'exact', 'balanced', or 'adaptive'"
        )
    for key in (
        "blur_output_fps",
        "rife_gpu_index",
        "deduplicate_range",
        "performance_samples",
        "analysis_width",
        "blur_taps",
    ):
        settings[key] = int(settings[key])
    for key in (
        "blur_amount",
        "blur_gamma",
        "input_timescale",
        "output_timescale",
        "brightness",
        "saturation",
        "contrast",
        "adaptive_motion_threshold",
        "adaptive_scene_threshold",
    ):
        settings[key] = float(settings[key])
    if settings["blur_output_fps"] < 1 or settings["blur_output_fps"] > 1000:
        raise ValueError("blur_output_fps must be between 1 and 1000")
    if settings["blur_amount"] < 0:
        raise ValueError("blur_amount must not be negative")
    if settings["blur_taps"] < 0 or (
        settings["blur_taps"] != 0 and settings["blur_taps"] % 2 != 1
    ):
        raise ValueError("blur_taps must be zero or a positive odd integer")
    if settings["performance_samples"] < 2 or settings["performance_samples"] > 32:
        raise ValueError("performance_samples must be between 2 and 32")
    if settings["analysis_width"] < 64 or settings["analysis_width"] > 1920:
        raise ValueError("analysis_width must be between 64 and 1920")
    motion_threshold = settings["adaptive_motion_threshold"]
    scene_threshold = settings["adaptive_scene_threshold"]
    if motion_threshold < 0 or motion_threshold >= 1:
        raise ValueError("adaptive_motion_threshold must be between 0 and 1")
    if scene_threshold <= motion_threshold or scene_threshold > 1:
        raise ValueError("adaptive_scene_threshold must be greater than the motion threshold and at most 1")
    weighting = str(settings["blur_weighting"]).strip()
    if weighting not in WEIGHTINGS:
        try:
            values = [float(item.strip()) for item in weighting.split(",")]
        except ValueError as exc:
            raise ValueError(f"Invalid blur_weighting: {weighting}") from exc
        if not values or sum(values) <= 0:
            raise ValueError("Custom blur weights must contain a positive sum")
    for key in ("interpolated_fps", "pre_interpolated_fps"):
        value = str(settings[key]).strip().casefold()
        try:
            numeric = Fraction(value[:-1] if value.endswith("x") else value)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"{key} must be an FPS or multiplier such as 4x") from exc
        if numeric <= 0:
            raise ValueError(f"{key} must be positive")


def apply_performance_policy(
    settings: dict[str, Any],
    *,
    explicit_interpolation_target: bool,
    source_fps: Fraction | None = None,
    explicit_performance_samples: bool = False,
    explicit_blur_amount: bool = False,
) -> dict[str, Any]:
    """Resolve a deterministic, vendor-neutral interpolation target.

    Automatic balanced/adaptive policy:

    * approved 60/90/120/144/180/240/360fps families use the Fast table;
    * other sources below 56fps use the smallest integer multiplier that
      reaches 200fps, then use the approved 60fps five-tap blur strategy;
    * other sources from 56fps through below 240fps target exactly 240fps;
    * 240fps and above: keep the native rate and skip main interpolation.

    An explicit target remains the highest-priority user choice. Exact mode
    preserves the configured Blur target. Explicit ``--performance-samples``
    retains the previous fixed output-rate multiplier behavior.
    """

    requested_target = str(settings["interpolated_fps"])
    mode = str(settings["performance_mode"])
    policy = "configured"
    blur_amount_policy = "explicit" if explicit_blur_amount else "configured"
    blur_taps_policy = "configured"
    blur_profile: str | None = None
    multiplier: int | float | None = None
    effective_ratio: Fraction | None = None
    minimum_target: int | None = None
    profile = (
        verified_frame_rate_profile(source_fps)
        if mode in {"balanced", "adaptive"}
        else None
    )
    if (
        bool(settings["interpolate"])
        and mode in {"balanced", "adaptive"}
        and not explicit_interpolation_target
    ):
        if explicit_performance_samples:
            target = int(settings["blur_output_fps"]) * int(settings["performance_samples"])
            settings["interpolated_fps"] = str(target)
            policy = "fixed-samples"
        elif source_fps is not None:
            if source_fps <= 0:
                raise ValueError("source_fps must be positive")
            minimum_target = 200 if source_fps < 56 else (240 if source_fps < 240 else None)
            if profile is not None:
                if "target_fps" in profile:
                    target = Fraction(profile["target_fps"])
                    effective_ratio = target / source_fps
                    multiplier = (
                        effective_ratio.numerator
                        if effective_ratio.denominator == 1
                        else float(effective_ratio)
                    )
                else:
                    multiplier = int(profile["multiplier"])
                    effective_ratio = Fraction(multiplier, 1)
                    target = source_fps * int(multiplier)
                policy = "fast-frame-rate-profile"
            elif source_fps >= 240:
                multiplier = 1
                effective_ratio = Fraction(1, 1)
                target = source_fps
                policy = "auto-native-rate"
            elif source_fps >= 56:
                target = Fraction(240, 1)
                effective_ratio = target / source_fps
                multiplier = (
                    effective_ratio.numerator
                    if effective_ratio.denominator == 1
                    else float(effective_ratio)
                )
                policy = "auto-fast-target"
            else:
                multiplier = max(1, ceil(Fraction(minimum_target, 1) / source_fps))
                effective_ratio = Fraction(multiplier, 1)
                target = source_fps * multiplier
                policy = "auto-integer-multiple"
            settings["interpolated_fps"] = str(target)
        else:
            target = int(settings["blur_output_fps"]) * int(settings["performance_samples"])
            settings["interpolated_fps"] = str(target)
            policy = "fixed-samples-fallback"
    elif explicit_interpolation_target:
        policy = "explicit-target"
    elif mode in {"original", "exact"}:
        policy = mode
    if profile is not None and not explicit_blur_amount:
        settings["blur_amount"] = float(profile["blur_amount"])
        settings["blur_taps"] = int(profile["blur_taps"])
        blur_profile = str(profile["name"])
        blur_taps_policy = "fast-frame-rate-profile"
        blur_amount_policy = (
            "auto-120fps-continuous"
            if profile["name"] == "fast-120fps"
            else "fast-frame-rate-profile"
        )
    elif (
        mode in {"balanced", "adaptive"}
        and source_fps is not None
        and source_fps < 56
        and not explicit_blur_amount
    ):
        settings["blur_amount"] = 1.0
        settings["blur_taps"] = 5
        blur_profile = "fast-60fps-blur"
        blur_amount_policy = "fast-60fps-blur"
        blur_taps_policy = "fast-60fps-blur"
    validate_settings(settings)
    return {
        "mode": mode,
        "build_flavor": BUILD_FLAVOR,
        "policy_id": POLICY_ID,
        "policy": policy,
        "profile": profile["name"] if profile is not None else None,
        "profile_status": profile.get("status") if profile is not None else None,
        "source_fps": str(source_fps) if source_fps is not None else None,
        "minimum_target": minimum_target,
        "multiplier": multiplier,
        "effective_ratio": str(effective_ratio) if effective_ratio is not None else None,
        "requested_target": requested_target,
        "effective_target": str(settings["interpolated_fps"]),
        "samples_per_output": int(settings["performance_samples"]),
        "blur_amount": float(settings["blur_amount"]),
        "blur_amount_policy": blur_amount_policy,
        "blur_taps": int(settings["blur_taps"]),
        "blur_taps_policy": blur_taps_policy,
        "blur_profile": blur_profile,
        "explicit_target": explicit_interpolation_target,
        "backend": "ncnn-vulkan",
    }
