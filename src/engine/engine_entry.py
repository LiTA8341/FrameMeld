from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from insight_engine import EngineConfig, SourceSpec, build_pipeline


source = SourceSpec(
    path=Path(vars().get("video_path", "")),
    fps_num=int(vars().get("fps_num", 0)),
    fps_den=int(vars().get("fps_den", 1)),
    full_range=vars().get("color_range", "") == "pc",
)
config = EngineConfig.from_mapping(json.loads(vars().get("settings", "{}")))
build_pipeline(source, config).set_output()
