from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import EngineError


@dataclass(frozen=True, slots=True)
class SourceSpec:
    path: Path
    fps_num: int
    fps_den: int
    full_range: bool = False


@dataclass(frozen=True, slots=True)
class SvpConfig:
    preset: str
    algorithm: int
    block_size: int
    mask_area: int
    use_gpu: bool
    manual: bool
    super_string: str
    vectors_string: str
    smooth_string: str


@dataclass(frozen=True, slots=True)
class InterpolationConfig:
    enabled: bool
    target: str
    method: str
    pre_enabled: bool
    pre_target: str
    model_path: Path
    gpu_index: int


@dataclass(frozen=True, slots=True)
class DeduplicationConfig:
    enabled: bool
    method: str
    search_range: int | None
    threshold: float
    debug: bool


@dataclass(frozen=True, slots=True)
class MotionBlurConfig:
    enabled: bool
    amount: float
    output_fps: int
    weighting: str
    gamma: float
    gaussian_std_dev: float
    gaussian_mean: float
    gaussian_bound: tuple[float, float]
    sample_count: int = 0


@dataclass(frozen=True, slots=True)
class TimescaleConfig:
    enabled: bool
    input_scale: float
    output_scale: float


@dataclass(frozen=True, slots=True)
class ColorConfig:
    enabled: bool
    brightness: float
    contrast: float
    saturation: float


@dataclass(frozen=True, slots=True)
class PerformanceConfig:
    mode: str
    samples_per_output: int
    adaptive_motion_threshold: float
    adaptive_scene_threshold: float
    analysis_width: int


@dataclass(frozen=True, slots=True)
class EngineConfig:
    interpolation: InterpolationConfig
    deduplication: DeduplicationConfig
    motion_blur: MotionBlurConfig
    timescale: TimescaleConfig
    color: ColorConfig
    svp: SvpConfig
    performance: PerformanceConfig
    gpu_decoding: bool

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "EngineConfig":
        raw_range = values.get("blur_weighting_gaussian_bound", "[0,2]")
        if isinstance(raw_range, str):
            raw_range = json.loads(raw_range)
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            raise EngineError("blur_weighting_gaussian_bound must contain two numbers")

        dedup_range = int(values.get("deduplicate_range", 2))
        if dedup_range == -1:
            dedup_range = None

        interpolation = InterpolationConfig(
            enabled=bool(values.get("interpolate", True)),
            target=str(values.get("interpolated_fps", "240")),
            method=str(values.get("interpolation_method", "rife")).lower(),
            pre_enabled=bool(values.get("pre_interpolate", False)),
            pre_target=str(values.get("pre_interpolated_fps", "120")),
            model_path=Path(str(values.get("rife_model", ""))),
            gpu_index=max(0, int(values.get("rife_gpu_index", 0))),
        )
        deduplication = DeduplicationConfig(
            enabled=bool(values.get("deduplicate", True)) and dedup_range != 0,
            method=str(values.get("deduplicate_method", "rife")).lower(),
            search_range=dedup_range,
            threshold=float(values.get("deduplicate_threshold", 0.001)),
            debug=bool(values.get("debug", False)),
        )
        motion_blur = MotionBlurConfig(
            enabled=bool(values.get("blur", True)),
            amount=float(values.get("blur_amount", 1.0)),
            output_fps=int(values.get("blur_output_fps", 60)),
            weighting=str(values.get("blur_weighting", "equal")),
            gamma=float(values.get("blur_gamma", 1.0)),
            gaussian_std_dev=float(values.get("blur_weighting_gaussian_std_dev", 1.0)),
            gaussian_mean=float(values.get("blur_weighting_gaussian_mean", 2.0)),
            gaussian_bound=(float(raw_range[0]), float(raw_range[1])),
            sample_count=int(values.get("blur_taps", 0)),
        )
        timescale = TimescaleConfig(
            enabled=bool(values.get("timescale", False)),
            input_scale=float(values.get("input_timescale", 1.0)),
            output_scale=float(values.get("output_timescale", 1.0)),
        )
        color = ColorConfig(
            enabled=bool(values.get("filters", False)),
            brightness=float(values.get("brightness", 1.0)),
            contrast=float(values.get("contrast", 1.0)),
            saturation=float(values.get("saturation", 1.0)),
        )
        svp = SvpConfig(
            preset=str(values.get("svp_interpolation_preset", "weak")).lower(),
            algorithm=int(values.get("svp_interpolation_algorithm", 13)),
            block_size=int(values.get("interpolation_blocksize", 8)),
            mask_area=int(values.get("interpolation_mask_area", 0)),
            use_gpu=bool(values.get("gpu_interpolation", True)),
            manual=bool(values.get("manual_svp", False)),
            super_string=str(values.get("super_string", "")),
            vectors_string=str(values.get("vectors_string", "")),
            smooth_string=str(values.get("smooth_string", "")),
        )
        performance = PerformanceConfig(
            mode=str(values.get("performance_mode", "balanced")).lower(),
            samples_per_output=int(values.get("performance_samples", 6)),
            adaptive_motion_threshold=float(values.get("adaptive_motion_threshold", 0.002)),
            adaptive_scene_threshold=float(values.get("adaptive_scene_threshold", 0.20)),
            analysis_width=int(values.get("analysis_width", 320)),
        )
        config = cls(
            interpolation=interpolation,
            deduplication=deduplication,
            motion_blur=motion_blur,
            timescale=timescale,
            color=color,
            svp=svp,
            performance=performance,
            gpu_decoding=bool(values.get("gpu_decoding", False)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.interpolation.method not in {"rife", "svp"}:
            raise EngineError("interpolation method must be rife or svp")
        if self.deduplication.method not in {"rife", "svp", "old"}:
            raise EngineError("deduplication method must be rife, svp, or old")
        if self.performance.mode not in {"original", "exact", "balanced", "adaptive"}:
            raise EngineError("performance mode must be original, exact, balanced, or adaptive")
        if self.motion_blur.output_fps <= 0:
            raise EngineError("motion-blur output FPS must be positive")
        if self.motion_blur.amount < 0:
            raise EngineError("motion-blur amount cannot be negative")
        if self.motion_blur.sample_count < 0 or (
            self.motion_blur.sample_count != 0 and self.motion_blur.sample_count % 2 != 1
        ):
            raise EngineError("motion-blur sample count must be zero or a positive odd integer")
        if self.deduplication.threshold < 0:
            raise EngineError("deduplication threshold cannot be negative")
        if self.deduplication.search_range is not None and self.deduplication.search_range < 1:
            raise EngineError("deduplication range must be -1, 0, or a positive integer")
        if self.motion_blur.gaussian_std_dev <= 0:
            raise EngineError("Gaussian standard deviation must be positive")
        if self.timescale.input_scale <= 0 or self.timescale.output_scale <= 0:
            raise EngineError("timescale values must be positive")
        if self.performance.samples_per_output < 2 or self.performance.samples_per_output > 32:
            raise EngineError("performance samples must be between 2 and 32")
        if self.performance.analysis_width < 64 or self.performance.analysis_width > 1920:
            raise EngineError("analysis width must be between 64 and 1920")
        if not 0 <= self.performance.adaptive_motion_threshold < 1:
            raise EngineError("adaptive motion threshold must be between 0 and 1")
        if not self.performance.adaptive_motion_threshold < self.performance.adaptive_scene_threshold <= 1:
            raise EngineError("adaptive scene threshold must exceed the motion threshold and be at most 1")
        if self.requires_rife and not self.interpolation.model_path.is_dir():
            raise EngineError(f"RIFE model directory not found: {self.interpolation.model_path}")

    @property
    def requires_rife(self) -> bool:
        return (
            (self.interpolation.enabled and (self.interpolation.method == "rife" or self.interpolation.pre_enabled))
            or (self.deduplication.enabled and self.deduplication.method == "rife")
        )
