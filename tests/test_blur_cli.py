from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


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
        self.assertIn("host-managed-encoder-fallback", capabilities["features"])

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

    def test_host_managed_failure_does_not_retry_inside_framemeld(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"test")
            detail = {"encoder": "h264_nvenc", "encoder_fallback": "libx264"}
            with (
                patch.object(
                    insight_blur,
                    "build_commands",
                    return_value=(["vspipe-hw"], ["ffmpeg-hw"], detail),
                ) as builder,
                patch.object(insight_blur, "run_pipeline", return_value=(1, 0)) as runner,
            ):
                code = insight_blur.main(
                    [
                        str(source),
                        str(output),
                        "--encoder",
                        "h264_nvenc",
                        "--host-managed-encoder-fallback",
                    ]
                )

            self.assertEqual(code, 1)
            builder.assert_called_once()
            runner.assert_called_once()


class UnicodeRuntimePathTests(unittest.TestCase):
    def test_build_uses_ascii_relative_rife_model_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "中文运行目录"
            source = root / "input.mp4"
            output = root / "output.mp4"
            required = (
                root / "lib" / "ffmpeg" / "ffmpeg-core.exe",
                root / "lib" / "ffmpeg" / "ffprobe.exe",
                root / "lib" / "vapoursynth" / "VSPipe.exe",
                root / "lib" / "engine_entry.py",
                root / "lib" / "models" / "rife-v4.26_ensembleFalse" / "flownet.bin",
                root / "lib" / "models" / "rife-v4.26_ensembleFalse" / "flownet.param",
            )
            for path in required:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")
            (root / "presets").mkdir()
            (root / "presets" / "balanced.json").write_text("{}", encoding="utf-8")
            source.write_bytes(b"test")
            args = insight_blur.parser().parse_args([str(source), str(output)])
            encoder_plan = SimpleNamespace(
                selected="libx264",
                fallback=None,
                gpu_vendors=(),
                attempts=(),
            )

            with (
                patch.object(insight_blur, "runtime_root", return_value=root),
                patch.object(
                    insight_blur,
                    "probe_video",
                    return_value={"fps_num": 60, "fps_den": 1, "color_range": "tv"},
                ),
                patch.object(insight_blur, "select_encoder", return_value=encoder_plan),
                patch.object(insight_blur, "encoder_args", return_value=["-c:v", "libx264"]),
            ):
                vspipe_command, _ffmpeg_command, detail = insight_blur.build_commands(args)

            expected = str(Path("models") / "rife-v4.26_ensembleFalse")
            self.assertEqual(detail["settings"]["rife_model"], expected)
            self.assertTrue(expected.isascii())
            settings_argument = next(value for value in vspipe_command if value.startswith("settings="))
            self.assertEqual(json.loads(settings_argument.removeprefix("settings="))["rife_model"], expected)

    def test_vspipe_runs_from_runtime_root(self) -> None:
        vspipe_process = Mock(stdout=Mock())
        vspipe_process.wait.return_value = 0
        ffmpeg_process = Mock()
        ffmpeg_process.wait.return_value = 0

        with (
            patch.object(insight_blur, "runtime_root", return_value=Path("F:/测试/FrameMeld")) as root,
            patch.object(
                insight_blur.subprocess,
                "Popen",
                side_effect=[vspipe_process, ffmpeg_process],
            ) as popen,
        ):
            self.assertEqual(insight_blur.run_pipeline(["vspipe"], ["ffmpeg"]), (0, 0))

        self.assertEqual(popen.call_args_list[0].kwargs["cwd"], root.return_value / "lib")
        self.assertNotIn("cwd", popen.call_args_list[1].kwargs)


if __name__ == "__main__":
    unittest.main()
