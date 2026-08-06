from __future__ import annotations

import math
from fractions import Fraction

import vapoursynth as vs
from vapoursynth import core

from .config import MotionBlurConfig
from .errors import EngineError
from .formats import process_in_format


def _normalize(weights: list[float]) -> list[float]:
    if not weights:
        raise EngineError("motion-blur weights cannot be empty")
    minimum = min(weights)
    if minimum < 0:
        weights = [value - minimum + 1.0 for value in weights]
    total = sum(weights)
    if total <= 0:
        raise EngineError("motion-blur weights must have a positive sum")
    return [value / total for value in weights]


def _range(count: int, start: float, end: float) -> list[float]:
    return [start] if count == 1 else [start + index * (end - start) / (count - 1) for index in range(count)]


def make_weights(count: int, config: MotionBlurConfig) -> list[float]:
    kind = config.weighting.strip().lower()
    if kind == "equal" or kind == "vegas" and count % 2 == 1:
        return [1.0 / count] * count
    if kind == "vegas":
        return _normalize([1.0] + [2.0] * (count - 2) + [1.0])
    if kind == "ascending":
        return _normalize([float(value) for value in range(1, count + 1)])
    if kind == "descending":
        return _normalize([float(value) for value in range(count, 0, -1)])
    if kind == "pyramid":
        half = (count - 1) / 2
        return _normalize([half - abs(index - half) + 1 for index in range(count)])
    if kind in {"gaussian", "gaussian_reverse", "gaussian_sym"}:
        start, end = config.gaussian_bound
        mean = config.gaussian_mean
        if kind == "gaussian_sym":
            extent = max(abs(start), abs(end))
            start, end, mean = -extent, extent, 0.0
        values = [
            math.exp(-((point - mean) ** 2) / (2 * config.gaussian_std_dev**2))
            for point in _range(count, start, end)
        ]
        result = _normalize(values)
        return list(reversed(result)) if kind == "gaussian_reverse" else result
    try:
        custom = [float(value.strip()) for value in config.weighting.split(",")]
    except ValueError as exc:
        raise EngineError(f"unknown motion-blur weighting: {config.weighting}") from exc
    positions = _range(count, 0, len(custom) - 0.1)
    return _normalize([custom[int(position)] for position in positions])


def interpolate_centered_weights(
    lower: list[float],
    upper: list[float],
    mix: float,
) -> list[float]:
    """Interpolate two odd, centered temporal kernels without shifting time."""

    if not 0.0 <= mix <= 1.0:
        raise EngineError("motion-blur weight mix must be between 0 and 1")
    if not lower or not upper or len(lower) % 2 != 1 or len(upper) % 2 != 1:
        raise EngineError("continuous motion-blur kernels must be non-empty and odd")
    if len(lower) > len(upper):
        lower, upper = upper, lower
        mix = 1.0 - mix
    difference = len(upper) - len(lower)
    if difference % 2 != 0:
        raise EngineError("continuous motion-blur kernels must share the same center")
    padding = difference // 2
    aligned_lower = [0.0] * padding + _normalize(lower) + [0.0] * padding
    normalized_upper = _normalize(upper)
    return _normalize(
        [
            (1.0 - mix) * low + mix * high
            for low, high in zip(aligned_lower, normalized_upper, strict=True)
        ]
    )


def make_motion_blur_weights(
    frame_gap: int,
    config: MotionBlurConfig,
) -> tuple[list[float], str]:
    """Build a centered blur kernel, including the tuned 5-to-7 transition.

    A 360-to-60 timeline has a six-frame gap.  For that path, amounts from
    0.75 through 1.0 continuously interpolate between centered five- and
    seven-tap kernels.  This makes 0.85 a real intermediate strength without
    introducing the half-frame phase shift of a direct even-tap kernel.
    """

    if frame_gap <= 0 or config.amount <= 0:
        return [], "disabled"
    if config.sample_count > 0:
        return make_weights(config.sample_count, config), f"{config.sample_count}-tap-profile"
    if frame_gap == 6 and 0.75 <= config.amount <= 1.0:
        mix = (config.amount - 0.75) / 0.25
        lower = make_weights(5, config)
        upper = make_weights(7, config)
        if mix <= 1e-12:
            return lower, "5-tap"
        if mix >= 1.0 - 1e-12:
            return upper, "7-tap"
        return (
            interpolate_centered_weights(lower, upper, mix),
            f"5-to-7 mix={mix:.3f}",
        )

    sample_count = int(frame_gap * config.amount)
    if sample_count <= 0:
        return [], "disabled"
    if sample_count % 2 == 0:
        sample_count += 1
    return make_weights(sample_count, config), f"{sample_count}-tap"


def nearest_frame_gap(source_fps: Fraction, output_fps: int) -> int:
    """Round a timeline ratio to the nearest frame gap, with halves upward."""

    if source_fps <= 0 or output_fps <= 0:
        raise EngineError("motion-blur frame rates must be positive")
    ratio = source_fps / output_fps
    return (2 * ratio.numerator + ratio.denominator) // (2 * ratio.denominator)


def _offset_clip(clip: vs.VideoNode, offset: int) -> vs.VideoNode:
    if offset > 0:
        return clip[offset:] + clip[-1] * offset
    if offset < 0:
        return clip[0] * -offset + clip[:offset]
    return clip


def weighted_average(clip: vs.VideoNode, weights: list[float]) -> vs.VideoNode:
    if len(weights) % 2 != 1:
        raise EngineError("motion-blur sample count must be odd")
    radius = len(weights) // 2
    clips = [_offset_clip(clip, offset) for offset in range(-radius, radius + 1)]
    expression = " ".join(f"src{index} {weight:.17g} *" for index, weight in enumerate(weights))
    expression += " " + "+ " * (len(weights) - 1)
    return core.akarin.Expr(clips, expression.strip())


def blend(clip: vs.VideoNode, full_range: bool, weights: list[float], gamma: float) -> vs.VideoNode:
    if gamma == 1.0:
        return weighted_average(clip, weights)

    def apply(working: vs.VideoNode) -> vs.VideoNode:
        linear = core.std.Expr(working, expr=f"x {gamma:.17g} pow")
        averaged = weighted_average(linear, weights)
        return core.std.Expr(averaged, expr=f"x {1.0 / gamma:.17g} pow")

    return process_in_format(clip, full_range, vs.RGBS, apply)
