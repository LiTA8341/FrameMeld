from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from encoder_selection import (  # noqa: E402
    build_encoder_candidates,
    encoder_args,
    encoder_family,
    select_encoder,
)


class EncoderCandidateTests(unittest.TestCase):
    def test_h264_auto_keeps_existing_default(self) -> None:
        self.assertEqual(
            build_encoder_candidates("auto", ["amd"]),
            ("h264_amf", "libx264"),
        )

    def test_hevc_aliases_use_matching_vendor(self) -> None:
        self.assertEqual(
            build_encoder_candidates("h265", ["nvidia"]),
            ("hevc_nvenc", "libx265"),
        )
        self.assertEqual(
            build_encoder_candidates("hevc", ["amd"]),
            ("hevc_amf", "libx265"),
        )
        self.assertEqual(
            build_encoder_candidates("hevc", ["intel"]),
            ("hevc_qsv", "libx265"),
        )

    def test_hybrid_gpu_order_is_preserved(self) -> None:
        self.assertEqual(
            build_encoder_candidates("hevc", ["nvidia", "intel"]),
            ("hevc_nvenc", "hevc_qsv", "libx265"),
        )

    def test_unknown_gpu_tries_each_vendor_before_software(self) -> None:
        self.assertEqual(
            build_encoder_candidates("hevc", []),
            ("hevc_nvenc", "hevc_amf", "hevc_qsv", "libx265"),
        )

    def test_explicit_hardware_retains_same_family_fallback(self) -> None:
        self.assertEqual(
            build_encoder_candidates("hevc_amf", ["nvidia"]),
            ("hevc_amf", "libx265"),
        )
        self.assertEqual(build_encoder_candidates("libx265", []), ("libx265",))

    def test_family_aliases(self) -> None:
        self.assertEqual(encoder_family("auto"), "h264")
        self.assertEqual(encoder_family("avc"), "h264")
        self.assertEqual(encoder_family("h265"), "hevc")
        self.assertEqual(encoder_family("hevc_qsv"), "hevc")


class EncoderSelectionTests(unittest.TestCase):
    ffmpeg = Path("ffmpeg.exe")

    @staticmethod
    def all_usable(_ffmpeg: Path, _encoder: str) -> tuple[bool, str]:
        return True, ""

    def test_amd_hevc_selects_amf_and_keeps_x265_fallback(self) -> None:
        plan = select_encoder(
            self.ffmpeg,
            "hevc",
            gpu_vendors=["amd"],
            compiled_encoders={"hevc_amf", "libx265"},
            encoder_probe=self.all_usable,
        )
        self.assertEqual(plan.selected, "hevc_amf")
        self.assertEqual(plan.fallback, "libx265")

    def test_intel_hevc_selects_qsv(self) -> None:
        plan = select_encoder(
            self.ffmpeg,
            "hevc",
            gpu_vendors=["intel"],
            compiled_encoders={"hevc_qsv", "libx265"},
            encoder_probe=self.all_usable,
        )
        self.assertEqual(plan.selected, "hevc_qsv")
        self.assertEqual(plan.fallback, "libx265")

    def test_failed_hardware_probe_selects_cpu(self) -> None:
        def probe(_ffmpeg: Path, encoder: str) -> tuple[bool, str]:
            return (encoder == "libx265", "driver unavailable" if encoder != "libx265" else "")

        plan = select_encoder(
            self.ffmpeg,
            "h265",
            gpu_vendors=["amd"],
            compiled_encoders={"hevc_amf", "libx265"},
            encoder_probe=probe,
        )
        self.assertEqual(plan.selected, "libx265")
        self.assertIsNone(plan.fallback)
        self.assertFalse(plan.attempts[0].usable)
        self.assertTrue(plan.attempts[1].usable)

    def test_missing_vendor_encoder_selects_cpu(self) -> None:
        plan = select_encoder(
            self.ffmpeg,
            "hevc",
            gpu_vendors=["intel"],
            compiled_encoders={"libx265"},
            encoder_probe=self.all_usable,
        )
        self.assertEqual(plan.selected, "libx265")
        self.assertFalse(plan.attempts[0].compiled)

    def test_no_usable_encoder_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "No usable HEVC encoder"):
            select_encoder(
                self.ffmpeg,
                "hevc",
                gpu_vendors=["amd"],
                compiled_encoders={"hevc_amf", "libx265"},
                encoder_probe=lambda _ffmpeg, _encoder: (False, "probe failed"),
            )


class EncoderArgumentTests(unittest.TestCase):
    def test_all_requested_encoders_have_cli_arguments(self) -> None:
        encoders = (
            "h264_nvenc",
            "h264_qsv",
            "h264_amf",
            "libx264",
            "hevc_nvenc",
            "hevc_qsv",
            "hevc_amf",
            "libx265",
        )
        for encoder in encoders:
            with self.subTest(encoder=encoder):
                args = encoder_args(encoder, 18)
                self.assertEqual(args[:2], ["-c:v", encoder])

    def test_h264_profiles_are_pinned_high_for_platform_delivery(self) -> None:
        for encoder in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264"):
            with self.subTest(encoder=encoder):
                args = encoder_args(encoder, 18)
                self.assertEqual(args[args.index("-profile:v") + 1], "high")

    def test_hevc_profiles_remain_encoder_defaults(self) -> None:
        for encoder in ("hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265"):
            with self.subTest(encoder=encoder):
                self.assertNotIn("-profile:v", encoder_args(encoder, 18))


if __name__ == "__main__":
    unittest.main()
