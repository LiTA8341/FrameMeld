from __future__ import annotations

from fractions import Fraction
import sys

import vapoursynth as vs
from vapoursynth import core

from .config import EngineConfig, SourceSpec
from .deduplication import repair_duplicates
from .formats import process_in_format, scale_fps
from .interpolation import (
    change_fps,
    change_fps_original,
    interpolate_rife,
    interpolate_rife_adaptive,
    interpolate_svp,
    target_fps,
)
from .motion_blur import blend, make_motion_blur_weights, nearest_frame_gap
from .errors import EngineError


def _load_source(source: SourceSpec, gpu_decoding: bool) -> vs.VideoNode:
    if gpu_decoding:
        return core.lsmas.LWLibavSource(
            source=source.path,
            cache=0,
            prefer_hw=3,
            fpsnum=source.fps_num,
            fpsden=source.fps_den,
        )
    return core.bs.VideoSource(
        source=source.path,
        cachemode=0,
        fpsnum=source.fps_num,
        fpsden=source.fps_den,
    )


def _interpolate(clip: vs.VideoNode, source: SourceSpec, config: EngineConfig) -> vs.VideoNode:
    options = config.interpolation
    if not options.enabled:
        return clip
    current = Fraction(clip.fps_num, clip.fps_den)
    target = target_fps(options.target, current)
    if options.method != "rife" and options.pre_enabled:
        pre_target = target_fps(options.pre_target, current)
        if current < pre_target:
            print(f"engine: pre-interpolate rife {current} -> {pre_target}", file=sys.stderr)
            clip = interpolate_rife(clip, source.full_range, pre_target, options.model_path, options.gpu_index)
            current = Fraction(clip.fps_num, clip.fps_den)
    if current >= target:
        return clip
    print(f"engine: interpolate {options.method} {current} -> {target}", file=sys.stderr)
    if options.method == "rife":
        if config.performance.mode == "adaptive":
            return interpolate_rife_adaptive(
                clip,
                source.full_range,
                target,
                options.model_path,
                options.gpu_index,
                config.performance,
            )
        return interpolate_rife(clip, source.full_range, target, options.model_path, options.gpu_index)
    return interpolate_svp(clip, source.full_range, target, config.svp)


def _apply_motion_blur(clip: vs.VideoNode, source: SourceSpec, config: EngineConfig) -> vs.VideoNode:
    options = config.motion_blur
    if not options.enabled:
        return clip
    if options.amount > 0:
        source_fps = Fraction(clip.fps_num, clip.fps_den)
        frame_gap = nearest_frame_gap(source_fps, options.output_fps)
        weights, description = make_motion_blur_weights(frame_gap, options)
        if weights:
            print(
                "engine: motion-blur "
                f"taps={len(weights)} profile={description} "
                f"amount={options.amount:.4f} weighting={options.weighting}",
                file=sys.stderr,
            )
            clip = blend(clip, source.full_range, weights, options.gamma)
    output_rate = Fraction(options.output_fps, 1)
    if config.performance.mode == "original":
        return change_fps_original(clip, output_rate)
    return change_fps(clip, output_rate)


def _adjust_color(clip: vs.VideoNode, source: SourceSpec, config: EngineConfig) -> vs.VideoNode:
    options = config.color
    if not options.enabled or (options.brightness == 1 and options.contrast == 1 and options.saturation == 1):
        return clip
    return process_in_format(
        clip,
        source.full_range,
        vs.YUV444PS,
        lambda working: core.adjust.Tweak(
            working,
            bright=options.brightness - 1,
            cont=options.contrast,
            sat=options.saturation,
        ),
    )


def build_pipeline(source: SourceSpec, config: EngineConfig) -> vs.VideoNode:
    if not source.path.is_file():
        raise EngineError(f"input video not found: {source.path}")
    clip = _load_source(source, config.gpu_decoding)
    if config.timescale.enabled and config.timescale.input_scale != 1:
        clip = scale_fps(clip, 1 / config.timescale.input_scale)
    clip = repair_duplicates(
        clip,
        source.full_range,
        config.deduplication,
        config.interpolation,
        config.svp,
        config.performance.analysis_width,
        original_compat=config.performance.mode == "original",
    )
    clip = _interpolate(clip, source, config)
    if config.timescale.enabled and config.timescale.output_scale != 1:
        clip = scale_fps(clip, config.timescale.output_scale)
    clip = _apply_motion_blur(clip, source, config)
    return _adjust_color(clip, source, config)
