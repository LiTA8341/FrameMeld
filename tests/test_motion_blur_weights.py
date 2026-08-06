from __future__ import annotations

import sys
import unittest
from fractions import Fraction
from pathlib import Path


ENGINE_SRC = Path(__file__).resolve().parents[1] / "src" / "engine"
if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from insight_engine.config import MotionBlurConfig  # noqa: E402
from insight_engine.motion_blur import make_motion_blur_weights, nearest_frame_gap  # noqa: E402


def config(amount: float) -> MotionBlurConfig:
    return MotionBlurConfig(
        enabled=True,
        amount=amount,
        output_fps=60,
        weighting="vegas",
        gamma=1.0,
        gaussian_std_dev=1.0,
        gaussian_mean=2.0,
        gaussian_bound=(0.0, 2.0),
    )


class ContinuousMotionBlurWeightTests(unittest.TestCase):
    def test_profile_can_force_centered_five_taps(self) -> None:
        options = config(1.0)
        object.__setattr__(options, "sample_count", 5)
        weights, description = make_motion_blur_weights(3, options)
        self.assertEqual(len(weights), 5)
        self.assertEqual(description, "5-tap-profile")

    def test_ntsc_360_timeline_rounds_to_six_frame_gap(self) -> None:
        self.assertEqual(nearest_frame_gap(Fraction(360000, 1001), 60), 6)

    def test_075_uses_centered_five_taps(self) -> None:
        weights, description = make_motion_blur_weights(6, config(0.75))
        self.assertEqual(len(weights), 5)
        self.assertEqual(description, "5-tap")
        self.assertAlmostEqual(sum(weights), 1.0)

    def test_085_blends_five_and_seven_taps_symmetrically(self) -> None:
        weights, description = make_motion_blur_weights(6, config(0.85))
        self.assertEqual(len(weights), 7)
        self.assertEqual(description, "5-to-7 mix=0.400")
        self.assertAlmostEqual(sum(weights), 1.0)
        self.assertEqual(weights, list(reversed(weights)))
        self.assertGreater(weights[1], weights[0])

    def test_100_preserves_existing_centered_seven_taps(self) -> None:
        weights, description = make_motion_blur_weights(6, config(1.0))
        self.assertEqual(len(weights), 7)
        self.assertEqual(description, "7-tap")
        self.assertAlmostEqual(sum(weights), 1.0)

    def test_0925_is_70pct_between_five_and_seven_taps(self) -> None:
        weights, description = make_motion_blur_weights(6, config(0.925))
        self.assertEqual(len(weights), 7)
        self.assertEqual(description, "5-to-7 mix=0.700")
        self.assertAlmostEqual(weights[0], 0.1)
        self.assertAlmostEqual(weights[1], 0.16)
        self.assertEqual(weights, list(reversed(weights)))


if __name__ == "__main__":
    unittest.main()
