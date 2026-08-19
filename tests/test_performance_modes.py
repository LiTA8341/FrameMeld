from __future__ import annotations

import sys
import unittest
from fractions import Fraction
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from engine_defaults import apply_performance_policy, merge_settings  # noqa: E402
from insight_blur import interpolation_target  # noqa: E402


MODEL = Path(__file__).resolve().parents[1] / "test-model"


class PerformancePolicyTests(unittest.TestCase):
    def settings(self, **overrides: object) -> dict[str, object]:
        return merge_settings(dict(overrides), MODEL)

    def test_60fps_uses_fast_four_times_profile(self) -> None:
        settings = self.settings(
            performance_mode="balanced",
            performance_samples=6,
            blur_output_fps=60,
            interpolated_fps="4x",
        )
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(60, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "240")
        self.assertEqual(detail["requested_target"], "4x")
        self.assertEqual(detail["effective_target"], "240")
        self.assertEqual(detail["multiplier"], 4)
        self.assertEqual(detail["minimum_target"], 240)
        self.assertEqual(detail["policy"], "fast-frame-rate-profile")
        self.assertEqual(detail["profile"], "fast-60fps")
        self.assertEqual(detail["profile_status"], "subjectively-confirmed")
        self.assertEqual(settings["blur_taps"], 5)
        self.assertEqual(detail["blur_profile"], "fast-60fps")
        self.assertEqual(detail["build_flavor"], "fast")
        self.assertEqual(detail["policy_id"], "source-relative-fast-v1")
        self.assertEqual(detail["backend"], "ncnn-vulkan")

    def test_adaptive_uses_the_same_integer_multiple_policy(self) -> None:
        settings = self.settings(
            performance_mode="adaptive",
            performance_samples=6,
            blur_output_fps=30,
            interpolated_fps="4x",
        )
        apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(90, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "270")

    def test_exact_preserves_blur_multiplier(self) -> None:
        settings = self.settings(performance_mode="exact", interpolated_fps="4x")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(240, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "4x")
        self.assertEqual(detail["effective_target"], "4x")

    def test_original_preserves_blur_multiplier_and_reports_policy(self) -> None:
        settings = self.settings(performance_mode="original", interpolated_fps="4x")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(240, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "4x")
        self.assertEqual(detail["effective_target"], "4x")
        self.assertEqual(detail["policy"], "original")

    def test_explicit_target_wins_over_balanced_policy(self) -> None:
        settings = self.settings(performance_mode="balanced", interpolated_fps="600")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=True,
            source_fps=Fraction(240, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "600")
        self.assertTrue(detail["explicit_target"])

    def test_240_input_stays_native(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(240, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "240")
        self.assertEqual(detail["multiplier"], 1)
        self.assertEqual(detail["profile"], "fast-240fps")
        self.assertIsNone(detail["minimum_target"])

    def test_120fps_uses_continuous_085_blur_amount(self) -> None:
        settings = self.settings(performance_mode="balanced", blur_amount=1.0)
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(120, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "240")
        self.assertEqual(settings["blur_amount"], 0.85)
        self.assertEqual(detail["blur_amount"], 0.85)
        self.assertEqual(detail["blur_amount_policy"], "auto-120fps-continuous")
        self.assertEqual(detail["profile"], "fast-120fps")
        self.assertEqual(settings["blur_taps"], 0)

    def test_144fps_uses_fast_double_and_70pct_blur(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(144, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "288")
        self.assertEqual(settings["blur_amount"], 0.925)
        self.assertEqual(settings["blur_taps"], 0)
        self.assertEqual(detail["profile"], "fast-144fps")
        self.assertEqual(detail["effective_ratio"], "2")
        self.assertEqual(detail["multiplier"], 2)

    def test_180fps_uses_verified_360_target_and_70pct_blur(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(180, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "360")
        self.assertEqual(settings["blur_amount"], 0.925)
        self.assertEqual(detail["profile"], "fast-180fps")
        self.assertEqual(detail["effective_ratio"], "2")
        self.assertEqual(detail["multiplier"], 2)

    def test_explicit_120fps_blur_amount_wins(self) -> None:
        settings = self.settings(performance_mode="balanced", blur_amount=0.9)
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            explicit_blur_amount=True,
            source_fps=Fraction(120, 1),
        )
        self.assertEqual(settings["blur_amount"], 0.9)
        self.assertEqual(detail["blur_amount_policy"], "explicit")

    def test_90fps_uses_verified_full_blur_amount(self) -> None:
        settings = self.settings(performance_mode="balanced", blur_amount=1.0)
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(90, 1),
        )
        self.assertEqual(settings["blur_amount"], 1.0)
        self.assertEqual(detail["blur_amount_policy"], "fast-frame-rate-profile")
        self.assertEqual(detail["profile"], "fast-90fps")
        self.assertEqual(settings["blur_taps"], 0)

    def test_native_360_input_is_preserved(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(360, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "360")
        self.assertEqual(detail["multiplier"], 1)
        self.assertEqual(detail["profile"], "fast-360fps")
        self.assertEqual(settings["blur_taps"], 7)

    def test_other_native_rate_at_or_above_300_is_preserved(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(400, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "400")
        self.assertEqual(detail["multiplier"], 1)
        self.assertEqual(detail["policy"], "auto-native-rate")
        self.assertIsNone(detail["minimum_target"])

    def test_random_low_rate_keeps_200_floor_and_uses_60fps_blur(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(53, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "212")
        self.assertEqual(detail["multiplier"], 4)
        self.assertEqual(detail["minimum_target"], 200)
        self.assertIsNone(detail["profile"])
        self.assertEqual(settings["blur_amount"], 1.0)
        self.assertEqual(settings["blur_taps"], 5)
        self.assertEqual(detail["blur_profile"], "fast-60fps-blur")

    def test_30fps_keeps_200_floor_and_uses_60fps_blur(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(30, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "210")
        self.assertEqual(detail["multiplier"], 7)
        self.assertEqual(detail["minimum_target"], 200)
        self.assertEqual(settings["blur_taps"], 5)

    def test_ntsc_60_rate_uses_fast_four_times_profile(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(60000, 1001),
        )
        self.assertEqual(settings["interpolated_fps"], "240000/1001")
        self.assertEqual(detail["multiplier"], 4)
        self.assertEqual(detail["profile"], "fast-60fps")

    def test_ntsc_90_and_120_rates_use_verified_profiles(self) -> None:
        cases = (
            (Fraction(90000, 1001), "270000/1001", 3, "fast-90fps", 1.0),
            (Fraction(120000, 1001), "240000/1001", 2, "fast-120fps", 0.85),
        )
        for rate, target, multiplier, profile, amount in cases:
            with self.subTest(rate=rate):
                settings = self.settings(performance_mode="balanced")
                detail = apply_performance_policy(
                    settings,
                    explicit_interpolation_target=False,
                    source_fps=rate,
                )
                self.assertEqual(settings["interpolated_fps"], target)
                self.assertEqual(detail["multiplier"], multiplier)
                self.assertEqual(detail["profile"], profile)
                self.assertEqual(settings["blur_amount"], amount)

    def test_verified_profile_tolerance_is_half_an_fps_inclusive(self) -> None:
        cases = (
            (Fraction(119, 2), "fast-60fps", 4),
            (Fraction(121, 2), "fast-60fps", 4),
            (Fraction(179, 2), "fast-90fps", 3),
            (Fraction(181, 2), "fast-90fps", 3),
            (Fraction(239, 2), "fast-120fps", 2),
            (Fraction(241, 2), "fast-120fps", 2),
            (Fraction(287, 2), "fast-144fps", 2),
            (Fraction(289, 2), "fast-144fps", 2),
            (Fraction(359, 2), "fast-180fps", 2),
            (Fraction(361, 2), "fast-180fps", 2),
            (Fraction(479, 2), "fast-240fps", 1),
            (Fraction(481, 2), "fast-240fps", 1),
            (Fraction(719, 2), "fast-360fps", 1),
            (Fraction(721, 2), "fast-360fps", 1),
        )
        for rate, profile, expected in cases:
            with self.subTest(rate=rate):
                settings = self.settings(performance_mode="balanced")
                detail = apply_performance_policy(
                    settings,
                    explicit_interpolation_target=False,
                    source_fps=rate,
                )
                self.assertEqual(detail["profile"], profile)
                self.assertEqual(detail["multiplier"], expected)

    def test_rate_outside_144_profile_uses_generic_240_target(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(143499, 1000),
        )
        self.assertIsNone(detail["profile"])
        self.assertEqual(detail["policy"], "auto-fast-target")
        self.assertEqual(settings["interpolated_fps"], "240")

    def test_5999_uses_60_profile_without_rounding_source_rate(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(5999, 100),
        )
        self.assertEqual(detail["profile"], "fast-60fps")
        self.assertEqual(settings["interpolated_fps"], "5999/25")
        self.assertEqual(detail["multiplier"], 4)
        self.assertEqual(settings["blur_taps"], 5)

    def test_11999_uses_120_continuous_blur_without_rounding(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(11999, 100),
        )
        self.assertEqual(detail["profile"], "fast-120fps")
        self.assertEqual(settings["interpolated_fps"], "11999/50")
        self.assertEqual(settings["blur_amount"], 0.85)

    def test_rate_outside_profile_tolerance_uses_generic_policy(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(59499, 1000),
        )
        self.assertIsNone(detail["profile"])
        self.assertEqual(detail["policy"], "auto-fast-target")
        self.assertEqual(settings["interpolated_fps"], "240")

    def test_299fps_stays_native(self) -> None:
        settings = self.settings(performance_mode="balanced")
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            source_fps=Fraction(299, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "299")
        self.assertEqual(detail["multiplier"], 1)
        self.assertEqual(detail["policy"], "auto-native-rate")

    def test_explicit_performance_samples_keeps_fixed_policy(self) -> None:
        settings = self.settings(performance_mode="balanced", performance_samples=8)
        detail = apply_performance_policy(
            settings,
            explicit_interpolation_target=False,
            explicit_performance_samples=True,
            source_fps=Fraction(60, 1),
        )
        self.assertEqual(settings["interpolated_fps"], "480")
        self.assertEqual(detail["policy"], "fixed-samples")

    def test_interpolation_target_helper_preserves_equal_rate(self) -> None:
        source_rate = Fraction(360, 1)
        self.assertEqual(interpolation_target("360", source_rate), source_rate)

    def test_invalid_threshold_order_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "adaptive_scene_threshold"):
            self.settings(
                adaptive_motion_threshold=0.2,
                adaptive_scene_threshold=0.1,
            )

    def test_invalid_analysis_width_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "analysis_width"):
            self.settings(analysis_width=32)


if __name__ == "__main__":
    unittest.main()
