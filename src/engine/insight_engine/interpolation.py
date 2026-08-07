from __future__ import annotations

import json
import math
import sys
from fractions import Fraction
from pathlib import Path

import vapoursynth as vs
from vapoursynth import core

from .analysis import low_resolution_luma, next_frame_differences, repeat_timeline
from .config import PerformanceConfig, SvpConfig
from .errors import EngineError
from .formats import process_in_format


LEGACY_SVP_PRESETS = {"weak", "film", "smooth", "animation"}
SVP_PRESETS = LEGACY_SVP_PRESETS | {"default", "test"}


def target_fps(value: str, current: Fraction) -> Fraction:
    text = str(value).strip().lower()
    try:
        target = current * Fraction(text[:-1]) if text.endswith("x") else Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise EngineError(f"invalid target FPS: {value}") from exc
    if target <= 0:
        raise EngineError("target FPS must be positive")
    return target


def _svp_rate(target: Fraction) -> int:
    return max(1, round(float(target)))


def generate_svp_strings(target: Fraction, config: SvpConfig, scene_detect: bool = False) -> tuple[str, str, str]:
    if config.preset not in SVP_PRESETS:
        raise EngineError(f"unknown SVP preset: {config.preset}")
    super_data: dict[str, object] = {"pel": 1, "gpu": config.use_gpu}
    vectors: dict[str, object] = {"block": {"w": config.block_size, "overlap": 0}}
    if config.preset == "test":
        vectors["main"] = {"search": {"type": 3, "satd": True, "coarse": {"type": 3}}}
    elif config.preset in LEGACY_SVP_PRESETS:
        coarse = {"distance": -1, "trymany": True, "bad": {"sad": 2000}} if config.preset == "weak" else {"distance": -10}
        vectors["main"] = {"search": {"distance": 0, "coarse": coarse}}
    smooth: dict[str, object] = {
        "rate": {"num": _svp_rate(target), "abs": True},
        "algo": config.algorithm,
        "mask": {"area": config.mask_area, "area_sharp": 1.2},
    }
    if not scene_detect:
        smooth["scene"] = {"blend": False, "mode": 0, "limits": {"blocks": 9999999}}
    return tuple(json.dumps(item, separators=(",", ":")) for item in (super_data, vectors, smooth))


def _run_svp(clip: vs.VideoNode, super_string: str, vectors_string: str, smooth_string: str) -> vs.VideoNode:
    super_clip = core.svp1.Super(clip, super_string)
    vectors = core.svp1.Analyse(super_clip["clip"], super_clip["data"], clip, vectors_string)
    return core.svp2.SmoothFps(
        clip,
        super_clip["clip"],
        super_clip["data"],
        vectors["clip"],
        vectors["data"],
        smooth_string,
    )


def _normalize_interpolation_length(
    result: vs.VideoNode,
    source: vs.VideoNode,
    target: Fraction,
) -> vs.VideoNode:
    source_rate = Fraction(source.fps_num, source.fps_den)
    exact_length = Fraction(len(source), 1) * target / source_rate
    expected_length = max(1, math.floor(exact_length + Fraction(1, 2)))
    if len(result) < expected_length:
        result = result + result[-1] * (expected_length - len(result))
    elif len(result) > expected_length:
        result = result[:expected_length]
    return core.std.AssumeFPS(result, fpsnum=target.numerator, fpsden=target.denominator)


def interpolate_svp(clip: vs.VideoNode, full_range: bool, target: Fraction, config: SvpConfig) -> vs.VideoNode:
    if config.manual:
        try:
            smooth = json.loads(config.smooth_string)
        except json.JSONDecodeError as exc:
            raise EngineError("manual SVP smooth_string is not valid JSON") from exc
        smooth.setdefault("rate", {"num": _svp_rate(target), "abs": True})
        strings = (config.super_string, config.vectors_string, json.dumps(smooth, separators=(",", ":")))
    else:
        strings = generate_svp_strings(target, config)
    result = process_in_format(clip, full_range, vs.YUV420P8, lambda working: _run_svp(working, *strings))
    return _normalize_interpolation_length(result, clip, target)


def interpolate_rife(
    clip: vs.VideoNode,
    full_range: bool,
    target: Fraction,
    model_path: Path,
    gpu_index: int,
) -> vs.VideoNode:
    if not model_path.is_dir():
        raise EngineError(f"RIFE model directory not found: {model_path}")
    result = process_in_format(
        clip,
        full_range,
        vs.RGBS,
        lambda working: core.rife.RIFE(
            working,
            fps_num=target.numerator,
            fps_den=target.denominator,
            model_path=str(model_path),
            gpu_id=gpu_index,
        ),
    )
    return _normalize_interpolation_length(result, clip, target)


def _linear_interpolation_timeline(
    clip: vs.VideoNode,
    target: Fraction,
    factor: int,
) -> vs.VideoNode:
    following = clip[1:] + clip[-1]
    samples = [clip]
    samples.extend(
        core.std.Merge(clip, following, weight=index / factor)
        for index in range(1, factor)
    )
    return _normalize_interpolation_length(core.std.Interleave(samples), clip, target)


def _held_interpolation_timeline(
    clip: vs.VideoNode,
    target: Fraction,
    factor: int,
) -> vs.VideoNode:
    return _normalize_interpolation_length(repeat_timeline(clip, factor), clip, target)


def interpolate_rife_adaptive(
    clip: vs.VideoNode,
    full_range: bool,
    target: Fraction,
    model_path: Path,
    gpu_index: int,
    performance: PerformanceConfig,
) -> vs.VideoNode:
    """Skip neural inference only where a cheap midpoint is visually equivalent."""

    if not model_path.is_dir():
        raise EngineError(f"RIFE model directory not found: {model_path}")
    source_rate = Fraction(clip.fps_num, clip.fps_den)
    ratio = target / source_rate
    if ratio.denominator != 1 or ratio.numerator < 2:
        print(
            f"engine: adaptive RIFE requires an integer multiplier; using full RIFE for {source_rate} -> {target}",
            file=sys.stderr,
        )
        return interpolate_rife(clip, full_range, target, model_path, gpu_index)
    factor = ratio.numerator
    analysis = low_resolution_luma(clip, performance.analysis_width)
    differences = _normalize_interpolation_length(
        repeat_timeline(next_frame_differences(analysis), factor),
        analysis,
        target,
    )

    def build(working: vs.VideoNode) -> vs.VideoNode:
        neural = _normalize_interpolation_length(
            core.rife.RIFE(
                working,
                fps_num=target.numerator,
                fps_den=target.denominator,
                model_path=str(model_path),
                gpu_id=gpu_index,
            ),
            working,
            target,
        )
        linear = _linear_interpolation_timeline(working, target, factor)
        held = _held_interpolation_timeline(working, target, factor)

        def select(n: int, f: vs.VideoFrame) -> vs.VideoNode:
            if n % factor == 0:
                return linear
            difference = float(f.props["PlaneStatsDiff"])
            if difference >= performance.adaptive_scene_threshold:
                return held
            if difference < performance.adaptive_motion_threshold:
                return linear
            return neural

        return core.std.FrameEval(linear, select, prop_src=differences)

    print(
        "engine: adaptive RIFE "
        f"factor={factor} motion={performance.adaptive_motion_threshold} "
        f"scene={performance.adaptive_scene_threshold} analysis={performance.analysis_width}px",
        file=sys.stderr,
    )
    return process_in_format(clip, full_range, vs.RGBS, build)


def change_fps(clip: vs.VideoNode, target: Fraction) -> vs.VideoNode:
    source = Fraction(clip.fps_num, clip.fps_den)
    if source == target:
        return clip
    ratio = source / target
    output_length = math.floor(len(clip) * target / source)
    if ratio.denominator == 1:
        selected = core.std.SelectEvery(clip, cycle=ratio.numerator, offsets=0)[:output_length]
        return core.std.AssumeFPS(selected, fpsnum=target.numerator, fpsden=target.denominator)

    factor = target / source

    def select_frame(n: int) -> vs.VideoNode:
        source_index = min(len(clip) - 1, math.floor(n / factor))
        return clip[source_index] * (output_length + 1)

    template = clip.std.BlankClip(length=output_length, fpsnum=target.numerator, fpsden=target.denominator)
    return core.std.FrameEval(template, eval=select_frame)


def change_fps_original(clip: vs.VideoNode, target: Fraction) -> vs.VideoNode:
    """Reproduce upstream Blur's FrameEval-based ChangeFPS graph exactly.

    This deliberately keeps the original floating-point factor and one-frame
    clip expansion. Replacing it with SelectEvery changes how temporal filters
    upstream of FrameEval are requested and therefore changes Blur's result.
    """

    factor = (target.numerator / target.denominator) * (clip.fps_den / clip.fps_num)
    output_length = math.floor(len(clip) * factor)

    def select_frame(n: int) -> vs.VideoNode:
        source_index = math.floor(n / factor)
        return clip[source_index] * (len(clip) + 100)

    template = clip.std.BlankClip(
        length=output_length,
        fpsnum=target.numerator,
        fpsden=target.denominator,
    )
    return template.std.FrameEval(eval=select_frame)
