"""Headless high-performance frame processing engine."""

from .config import EngineConfig, SourceSpec
from .pipeline import build_pipeline

__all__ = ["EngineConfig", "SourceSpec", "build_pipeline"]
