from __future__ import annotations

from fractions import Fraction
from typing import Callable

import vapoursynth as vs
from vapoursynth import core

from .errors import EngineError


def process_in_format(
    clip: vs.VideoNode,
    full_range: bool,
    target_format: int,
    operation: Callable[[vs.VideoNode], vs.VideoNode],
) -> vs.VideoNode:
    if clip.format is None:
        raise EngineError("variable-format clips are not supported")
    original_format = clip.format
    conversion_needed = original_format.id != target_format
    working = clip
    if conversion_needed:
        arguments: dict[str, object] = {
            "format": target_format,
            "range_in": full_range,
            "range": full_range,
        }
        if target_format == vs.RGBS and original_format.color_family == vs.YUV:
            arguments["matrix_in_s"] = "709"
        working = core.resize.Point(working, **arguments)

    working = operation(working)
    if not conversion_needed:
        return working

    arguments = {
        "format": original_format.id,
        "range_in": full_range,
        "range": full_range,
    }
    if target_format == vs.RGBS and original_format.color_family == vs.YUV:
        arguments["matrix_s"] = "709"
    return core.resize.Point(working, **arguments)


def scale_fps(clip: vs.VideoNode, multiplier: float) -> vs.VideoNode:
    target = Fraction(clip.fps_num, clip.fps_den) * Fraction(str(multiplier))
    return core.std.AssumeFPS(clip, fpsnum=target.numerator, fpsden=target.denominator)
