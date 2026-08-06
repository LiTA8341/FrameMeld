from __future__ import annotations

import vapoursynth as vs
from vapoursynth import core

from .errors import EngineError


def low_resolution_luma(clip: vs.VideoNode, analysis_width: int) -> vs.VideoNode:
    """Return a small, deterministic luma clip for motion/drop detection."""

    if clip.format is None:
        raise EngineError("variable-format clips are not supported")
    width = min(clip.width, analysis_width)
    height = max(1, round(clip.height * width / clip.width))
    arguments: dict[str, object] = {
        "width": width,
        "height": height,
        "format": vs.GRAY8,
    }
    if clip.format.color_family == vs.RGB:
        arguments["matrix_s"] = "709"
    return core.resize.Bilinear(clip, **arguments)


def previous_frame_differences(analysis: vs.VideoNode) -> vs.VideoNode:
    previous = analysis[0] + analysis
    return core.std.PlaneStats(analysis, previous)


def next_frame_differences(analysis: vs.VideoNode) -> vs.VideoNode:
    following = analysis[1:] + analysis[-1]
    return core.std.PlaneStats(analysis, following)


def repeat_timeline(clip: vs.VideoNode, factor: int) -> vs.VideoNode:
    if factor < 1:
        raise EngineError("timeline repetition factor must be positive")
    return clip if factor == 1 else core.std.Interleave([clip] * factor)
