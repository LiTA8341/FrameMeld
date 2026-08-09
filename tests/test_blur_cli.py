from __future__ import annotations

import json
import io
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
        self.assertIn("structured-status-json-v1", capabilities["features"])
        self.assertIn("device-diagnostics-json-v1", capabilities["features"])

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

    def test_structured_status_flag_is_forwarded(self) -> None:
        translated = framemeld_cli.translate(
            ["-i", "input.mp4", "--status-json-lines", "output.mp4"]
        )
        self.assertEqual(
            translated,
            ["input.mp4", "output.mp4", "--status-json-lines"],
        )

    def test_intel_qsv_device_status_is_an_independent_unbound_branch(self) -> None:
        status = insight_blur.encoder_device_status(
            "h264_qsv",
            None,
            {
                "name": "Intel Arc Test",
                "vendor": "intel",
                "stable_id": "luid:intel-test",
            },
        )

        self.assertEqual(status["vendor"], "intel")
        self.assertEqual(status["backend"], "h264_qsv")
        self.assertEqual(status["selection"], "system-default")
        self.assertFalse(status["explicit_binding_supported"])
        self.assertIsNone(status["index"])
        self.assertEqual(
            status["host_planned_adapter"]["stable_id"],
            "luid:intel-test",
        )

    def test_amd_and_nvidia_device_status_branches_remain_distinct(self) -> None:
        amf = insight_blur.encoder_device_status("h264_amf", None)
        nvenc = insight_blur.encoder_device_status("h264_nvenc", 2)

        self.assertEqual(amf["vendor"], "amd")
        self.assertEqual(amf["selection"], "system-default")
        self.assertFalse(amf["explicit_binding_supported"])
        self.assertEqual(nvenc["vendor"], "nvidia")
        self.assertEqual(nvenc["selection"], "explicit")
        self.assertTrue(nvenc["explicit_binding_supported"])
        self.assertEqual(nvenc["index"], 2)

    def test_vulkan_inventory_keeps_missing_stable_identity_explicit(self) -> None:
        listing = """
[AVHWDeviceContext @ 000001] GPU listing:
[AVHWDeviceContext @ 000001]     0: AMD Radeon RX 7800 XT (discrete) (0x747e)
[AVHWDeviceContext @ 000001]     1: Intel(R) Arc(TM) A770 Graphics (discrete) (0x56a0)
"""
        completed = SimpleNamespace(returncode=1, stdout="", stderr=listing)
        with patch.object(insight_blur.subprocess, "run", return_value=completed):
            inventory = insight_blur.probe_vulkan_inventory(Path("ffmpeg-core.exe"))

        self.assertEqual(inventory["status"], "succeeded")
        self.assertEqual(inventory["devices"][0]["device_id"], "747E")
        self.assertEqual(inventory["devices"][0]["vendor"], "amd")
        self.assertIsNone(inventory["devices"][0]["uuid"])
        self.assertIsNone(inventory["devices"][0]["luid"])
        self.assertFalse(inventory["devices"][0]["stable_identity_available"])

    def test_device_mapping_is_candidate_not_exact_without_luid(self) -> None:
        inventory = {
            "devices": [
                {
                    "index": 0,
                    "name": "AMD Radeon RX 7800 XT",
                    "vendor": "amd",
                    "device_id": "747E",
                    "uuid": None,
                    "luid": None,
                }
            ]
        }
        mapping = insight_blur.map_host_adapter_to_vulkan(
            {
                "name": "AMD Radeon RX 7800 XT",
                "vendor": "amd",
                "device_id": "747E",
                "luid": "0000000000001234",
            },
            inventory,
            0,
        )

        self.assertEqual(mapping["outcome"], "candidate")
        self.assertEqual(mapping["confidence"], "medium")
        self.assertFalse(mapping["exact_mapping_available"])
        self.assertFalse(mapping["index_spaces"]["verified_equal"])

    def test_same_model_vulkan_devices_are_reported_ambiguous(self) -> None:
        inventory = {
            "devices": [
                {"index": index, "name": "AMD Radeon RX 7800 XT", "vendor": "amd", "device_id": "747E"}
                for index in (0, 1)
            ]
        }
        mapping = insight_blur.map_host_adapter_to_vulkan(
            {"name": "AMD Radeon RX 7800 XT", "vendor": "amd", "device_id": "747E"},
            inventory,
            0,
        )

        self.assertEqual(mapping["outcome"], "ambiguous")
        self.assertEqual(mapping["candidate_vulkan_indices"], [0, 1])


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
                    side_effect=[
                        insight_blur.PipelineResult(1, 0, ffmpeg_stderr="[hevc_nvenc] encoder failed"),
                        insight_blur.PipelineResult(0, 0),
                    ],
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
                patch.object(
                    insight_blur,
                    "run_pipeline",
                    return_value=insight_blur.PipelineResult(
                        1,
                        0,
                        ffmpeg_stderr="[h264_nvenc] encoder failed",
                    ),
                ) as runner,
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

    def test_frame_engine_failure_is_reported_without_encoder_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"test")
            detail = {
                "encoder": "h264_amf",
                "encoder_fallback": "libx264",
                "devices": {"rife": {"index": 0}, "encoder": {"backend": "h264_amf"}},
            }
            result = insight_blur.PipelineResult(
                1,
                1,
                vspipe_stderr="vapoursynth.Error: RIFE: failed to load model",
            )
            with (
                patch.object(
                    insight_blur,
                    "build_commands",
                    return_value=(["vspipe"], ["ffmpeg"], detail),
                ) as builder,
                patch.object(insight_blur, "run_pipeline", return_value=result),
                patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                code = insight_blur.main(
                    [str(source), str(output), "--status-json-lines"]
                )

            self.assertEqual(code, 1)
            builder.assert_called_once()
            events = [
                json.loads(line.removeprefix(insight_blur.STATUS_PREFIX))
                for line in stderr.getvalue().splitlines()
                if line.startswith(insight_blur.STATUS_PREFIX)
            ]
            self.assertEqual(events[-1]["failure_domain"], "frame_engine")

    def test_host_managed_encoder_preflight_failure_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            output = Path(directory) / "output.mp4"
            source.write_bytes(b"test")
            with (
                patch.object(
                    insight_blur,
                    "build_commands",
                    side_effect=insight_blur.EncoderPreflightFailure(
                        "h264_amf",
                        "AMF runtime unavailable",
                    ),
                ),
                patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                code = insight_blur.main(
                    [
                        str(source),
                        str(output),
                        "--encoder",
                        "h264_amf",
                        "--host-managed-encoder-fallback",
                        "--status-json-lines",
                        "--host-encoder-adapter-json",
                        '{"name":"AMD Radeon RX Test","stable_id":"luid:test"}',
                    ]
                )

            self.assertEqual(code, 2)
            events = [
                json.loads(line.removeprefix(insight_blur.STATUS_PREFIX))
                for line in stderr.getvalue().splitlines()
                if line.startswith(insight_blur.STATUS_PREFIX)
            ]
            self.assertEqual(events[-1]["event"], "startup_failed")
            self.assertEqual(events[-1]["failure_domain"], "encoder")
            self.assertEqual(events[-1]["encoder"], "h264_amf")
            self.assertEqual(
                events[-1]["devices"]["encoder"]["host_planned_adapter"]["stable_id"],
                "luid:test",
            )


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
            args = insight_blur.parser().parse_args(
                [
                    str(source),
                    str(output),
                    "--host-encoder-adapter-json",
                    '{"name":"AMD Radeon RX Test","stable_id":"luid:test"}',
                ]
            )
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
            self.assertEqual(
                detail["devices"]["encoder"]["host_planned_adapter"]["stable_id"],
                "luid:test",
            )

    def test_vspipe_runs_from_runtime_root(self) -> None:
        vspipe_process = Mock(stdout=Mock(), stderr=io.BytesIO(b""))
        vspipe_process.wait.return_value = 0
        vspipe_process.poll.return_value = 0
        ffmpeg_process = Mock(stderr=io.BytesIO(b""))
        ffmpeg_process.wait.return_value = 0

        with (
            patch.object(insight_blur, "runtime_root", return_value=Path("F:/测试/FrameMeld")) as root,
            patch.object(
                insight_blur.subprocess,
                "Popen",
                side_effect=[vspipe_process, ffmpeg_process],
            ) as popen,
        ):
            result = insight_blur.run_pipeline(["vspipe"], ["ffmpeg"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.ffmpeg_code, 0)
        self.assertEqual(result.vspipe_code, 0)

        self.assertEqual(popen.call_args_list[0].kwargs["cwd"], root.return_value / "lib")
        self.assertNotIn("cwd", popen.call_args_list[1].kwargs)

    def test_vspipe_progress_is_emitted_as_structured_status(self) -> None:
        vspipe_process = Mock(stdout=Mock(), stderr=io.BytesIO(b"Frame: 3/10\rFrame: 10/10\r"))
        vspipe_process.wait.return_value = 0
        vspipe_process.poll.return_value = 0
        ffmpeg_process = Mock(stderr=io.BytesIO(b""))
        ffmpeg_process.wait.return_value = 0

        with (
            patch.object(
                insight_blur.subprocess,
                "Popen",
                side_effect=[vspipe_process, ffmpeg_process],
            ),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            result = insight_blur.run_pipeline(
                ["vspipe"],
                ["ffmpeg"],
                status_json_lines=True,
            )

        self.assertEqual(result.processed_frames, 10)
        self.assertEqual(result.total_frames, 10)
        events = [
            json.loads(line.removeprefix(insight_blur.STATUS_PREFIX))
            for line in stderr.getvalue().replace("\r", "\n").splitlines()
            if line.startswith(insight_blur.STATUS_PREFIX)
        ]
        self.assertTrue(any(event.get("event") == "progress" for event in events))
        self.assertEqual(events[-1]["processed_frames"], 10)

    def test_first_frame_and_packet_observations_are_structured(self) -> None:
        vspipe_process = Mock(
            stdout=Mock(),
            stderr=io.BytesIO(b"Frame: 1/15\rOutput 15 frames in 0.01 seconds\n"),
        )
        vspipe_process.wait.return_value = 0
        vspipe_process.poll.return_value = 0
        ffmpeg_process = Mock(
            stderr=io.BytesIO(b"frame=1\ntotal_size=4096\nprogress=continue\n")
        )
        ffmpeg_process.wait.return_value = 0

        with (
            patch.object(
                insight_blur.subprocess,
                "Popen",
                side_effect=[vspipe_process, ffmpeg_process],
            ),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            result = insight_blur.run_pipeline(
                ["vspipe"],
                ["ffmpeg"],
                status_json_lines=True,
            )

        events = [
            json.loads(line.removeprefix(insight_blur.STATUS_PREFIX))
            for line in stderr.getvalue().replace("\r", "\n").splitlines()
            if line.startswith(insight_blur.STATUS_PREFIX)
        ]
        self.assertIsNotNone(result.first_frame_ms)
        self.assertIsNotNone(result.first_packet_ms)
        self.assertEqual(result.processed_frames, 15)
        self.assertEqual(result.total_frames, 15)
        self.assertEqual(result.output_bytes, 4096)
        self.assertTrue(any(event.get("event") == "first_frame" for event in events))
        self.assertTrue(any(event.get("event") == "first_packet" for event in events))


if __name__ == "__main__":
    unittest.main()
