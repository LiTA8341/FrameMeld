from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Callable

import vapoursynth as vs
from vapoursynth import core

from .analysis import low_resolution_luma, previous_frame_differences
from .config import DeduplicationConfig, InterpolationConfig, SvpConfig
from .formats import process_in_format
from .interpolation import generate_svp_strings


@dataclass(slots=True)
class _RepairState:
    interpolation: vs.VideoNode | None = None
    previous_unique: int = 0
    next_unique: int = 0
    gap: int = 0


def _find_next_unique(
    clip: vs.VideoNode,
    duplicate_index: int,
    threshold: float,
    search_range: int | None,
) -> int | None:
    duplicate = clip[duplicate_index]
    final_index = len(clip) - 1
    if search_range is not None:
        final_index = min(final_index, duplicate_index + search_range)
    for index in range(duplicate_index + 1, final_index + 1):
        difference = core.std.PlaneStats(clip[index], duplicate)
        frame = next(iter(difference.frames()))
        if frame.props["PlaneStatsDiff"] >= threshold:
            return index
    return None


def _rife_creator(model_path: str, gpu_index: int) -> Callable[[vs.VideoNode, int], vs.VideoNode]:
    def create(unique_pair: vs.VideoNode, gap: int) -> vs.VideoNode:
        interpolation = core.rife.RIFE(
            unique_pair,
            fps_num=gap,
            fps_den=1,
            model_path=model_path,
            gpu_id=gpu_index,
        )
        return interpolation[1 : 1 + gap]

    return create


def _svp_creator(svp: SvpConfig) -> Callable[[vs.VideoNode, int], vs.VideoNode]:
    def create(unique_pair: vs.VideoNode, gap: int) -> vs.VideoNode:
        strings = generate_svp_strings(Fraction(gap, 1), svp)
        super_clip = core.svp1.Super(unique_pair, strings[0])
        vectors = core.svp1.Analyse(super_clip["clip"], super_clip["data"], unique_pair, strings[1])
        interpolation = core.svp2.SmoothFps(
            unique_pair,
            super_clip["clip"],
            super_clip["data"],
            vectors["clip"],
            vectors["data"],
            strings[2],
            src=unique_pair,
            fps=unique_pair.fps,
        )
        return interpolation[1 : 1 + gap]

    return create


def _adaptive_repair(
    clip: vs.VideoNode,
    analysis: vs.VideoNode,
    config: DeduplicationConfig,
    creator: Callable[[vs.VideoNode, int], vs.VideoNode],
) -> vs.VideoNode:
    one_fps = core.std.AssumeFPS(clip, fpsnum=1, fpsden=1)
    analysis_one_fps = core.std.AssumeFPS(analysis, fpsnum=1, fpsden=1)
    differences = previous_frame_differences(analysis)
    state = _RepairState()

    def build_repair(duplicate_index: int) -> None:
        state.previous_unique = duplicate_index - 1
        found = _find_next_unique(
            analysis_one_fps,
            duplicate_index,
            config.threshold,
            config.search_range,
        )
        if found is None:
            state.interpolation = None
            return
        state.next_unique = found
        state.gap = state.next_unique - state.previous_unique
        unique_pair = one_fps[state.previous_unique] + one_fps[state.next_unique]
        state.interpolation = core.std.AssumeFPS(creator(unique_pair, state.gap), fpsnum=1, fpsden=1)

    def handle(n: int, f: vs.VideoFrame) -> vs.VideoNode:
        difference = float(f.props["PlaneStatsDiff"])
        if n == 0 or difference >= config.threshold:
            state.interpolation = None
            return clip
        if state.interpolation is None:
            build_repair(n)
        if state.interpolation is None:
            return clip
        before = core.std.Trim(one_fps, first=0, last=state.previous_unique)
        after = core.std.Trim(one_fps, first=state.next_unique)
        repaired = core.std.AssumeFPS(before + state.interpolation + after, src=clip)
        if config.debug:
            return core.text.Text(
                repaired,
                text=f"duplicate, {state.gap - 1} gap, diff: {difference:.4f}",
                alignment=8,
            )
        return repaired

    return core.std.FrameEval(clip, handle, prop_src=differences)


def repair_rife(
    clip: vs.VideoNode,
    full_range: bool,
    config: DeduplicationConfig,
    interpolation: InterpolationConfig,
    analysis_width: int,
    original_compat: bool = False,
) -> vs.VideoNode:
    creator = _rife_creator(str(interpolation.model_path), interpolation.gpu_index)
    if original_compat:
        return process_in_format(
            clip,
            full_range,
            vs.RGBS,
            lambda working: _adaptive_repair(working, working, config, creator),
        )
    analysis = low_resolution_luma(clip, analysis_width)
    return process_in_format(
        clip,
        full_range,
        vs.RGBS,
        lambda working: _adaptive_repair(working, analysis, config, creator),
    )


def repair_svp(
    clip: vs.VideoNode,
    full_range: bool,
    config: DeduplicationConfig,
    svp: SvpConfig,
    analysis_width: int,
    original_compat: bool = False,
) -> vs.VideoNode:
    creator = _svp_creator(svp)
    if original_compat:
        return process_in_format(
            clip,
            full_range,
            vs.YUV420P8,
            lambda working: _adaptive_repair(working, working, config, creator),
        )
    analysis = low_resolution_luma(clip, analysis_width)
    return process_in_format(
        clip,
        full_range,
        vs.YUV420P8,
        lambda working: _adaptive_repair(working, analysis, config, creator),
    )


def repair_legacy(
    clip: vs.VideoNode,
    config: DeduplicationConfig,
    analysis_width: int,
    original_compat: bool = False,
) -> vs.VideoNode:
    analysis = clip if original_compat else low_resolution_luma(clip, analysis_width)
    differences = previous_frame_differences(analysis)
    super_clip = core.mv.Super(clip)
    forward = core.mv.Analyse(super_clip, isb=False)
    backward = core.mv.Analyse(super_clip, isb=True)
    repaired = core.mv.FlowInter(clip, super_clip, mvbw=backward, mvfw=forward, ml=1)

    def select(n: int, f: vs.VideoFrame) -> vs.VideoNode:
        del n
        difference = float(f.props["PlaneStatsDiff"])
        selected = repaired if difference < config.threshold else clip
        if config.debug and selected is repaired:
            return core.text.Text(selected, f"interpolated, diff: {difference:.3f}", alignment=8)
        return selected

    return core.std.FrameEval(clip, select, prop_src=differences)


def repair_duplicates(
    clip: vs.VideoNode,
    full_range: bool,
    config: DeduplicationConfig,
    interpolation: InterpolationConfig,
    svp: SvpConfig,
    analysis_width: int,
    original_compat: bool = False,
) -> vs.VideoNode:
    if not config.enabled:
        return clip
    if config.method == "rife":
        return repair_rife(
            clip,
            full_range,
            config,
            interpolation,
            analysis_width,
            original_compat,
        )
    if config.method == "svp":
        return repair_svp(
            clip,
            full_range,
            config,
            svp,
            analysis_width,
            original_compat,
        )
    return repair_legacy(clip, config, analysis_width, original_compat)
