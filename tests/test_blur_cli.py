from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import framemeld_cli  # noqa: E402
import insight_blur  # noqa: E402


class FfmpegCliTranslationTests(unittest.TestCase):
    def test_capabilities_identify_public_protocol_and_license(self) -> None:
        capabilities = json.loads(framemeld_cli.capabilities_json())
        self.assertEqual(capabilities["protocol"], "org.framemeld.cli")
        self.assertEqual(capabilities["api_version"], 1)
        self.assertEqual(capabilities["license"], "GPL-3.0-only")
        self.assertIn("motion-blur", capabilities["features"])

    def test_h265_alias_is_forwarded_to_encoder_planner(self) -> None:
        translated = framemeld_cli.translate(
            ["-i", "input.mp4", "-c:v", "h265", "-cq", "18", "output.mp4"]
        )
        self.assertEqual(
            translated,
            ["input.mp4", "output.mp4", "--encoder", "h265", "--quality", "18"],
        )

    def test_host_final_pass_options_are_forwarded(self) -> None:
        translated = framemeld_cli.translate(
            [
                "-y",
                "-i",
                "input.mp4",
                "-c:v",
                "h264_nvenc",
                "-cq",
                "20",
                "-gpu",
                "1",
                "-c:a",
                "copy",
                "output.mp4",
            ]
        )
        self.assertEqual(
            translated,
            [
                "input.mp4",
                "output.mp4",
                "--encoder",
                "h264_nvenc",
                "--quality",
                "20",
                "--encoder-device",
                "1",
                "--audio-codec",
                "copy",
            ],
        )


class FullExportFallbackTests(unittest.TestCase):
    def test_hardware_failure_rebuilds_pipeline_with_software_encoder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"test")
            first_detail = {"encoder": "hevc_nvenc", "encoder_fallback": "libx265"}
            fallback_detail = {"encoder": "libx265", "encoder_fallback": None}
            with (
                patch.object(
                    insight_blur,
                    "build_commands",
                    side_effect=[
                        (["vspipe-hw"], ["ffmpeg-hw"], first_detail),
                        (["vspipe-cpu"], ["ffmpeg-cpu"], fallback_detail),
                    ],
                ) as builder,
                patch.object(
                    insight_blur,
                    "run_pipeline",
                    side_effect=[(1, 0), (0, 0)],
                ) as runner,
            ):
                code = insight_blur.main([str(source), str(output), "--encoder", "h265"])

            self.assertEqual(code, 0)
            self.assertEqual(runner.call_count, 2)
            self.assertEqual(builder.call_args_list[1].kwargs["encoder_override"], "libx265")


if __name__ == "__main__":
    unittest.main()
