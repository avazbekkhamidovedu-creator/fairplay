"""Unit tests for judge.utils.calibration — spline, presets, round_half."""
from unittest import TestCase

from judge.utils.calibration import apply_preset, apply_spline, calibrate, round_half


class RoundHalfTests(TestCase):
    def test_exact_half(self):
        self.assertEqual(round_half(0.5), 0.5)
        self.assertEqual(round_half(1.5), 1.5)
        self.assertEqual(round_half(2.5), 2.5)

    def test_round_up_to_half(self):
        self.assertEqual(round_half(0.3), 0.5)
        self.assertEqual(round_half(0.26), 0.5)
        self.assertEqual(round_half(1.74), 1.5)

    def test_round_down_to_half(self):
        self.assertEqual(round_half(0.24), 0.0)
        self.assertEqual(round_half(1.24), 1.0)
        self.assertEqual(round_half(3.74), 3.5)

    def test_zero(self):
        self.assertEqual(round_half(0.0), 0.0)

    def test_negative(self):
        self.assertEqual(round_half(-0.3), -0.5)
        self.assertEqual(round_half(-1.2), -1.0)


class ApplySplineTests(TestCase):
    def test_max_score_5_known_raw(self):
        """Verify spline at anchor points for max_score=5."""
        # raw=0 → spline ≈ 0
        self.assertAlmostEqual(apply_spline(0.0, 5.0), 0.0, delta=0.1)
        # raw=5 → spline = 5 (last anchor)
        self.assertAlmostEqual(apply_spline(5.0, 5.0), 5.0, delta=0.1)
        # raw=2.5 (midpoint) → spline somewhere between 0 and 5
        result = apply_spline(2.5, 5.0)
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, 5.0)

    def test_max_score_10_known_raw(self):
        """Verify spline at anchor points for max_score=10."""
        self.assertAlmostEqual(apply_spline(0.0, 10.0), 0.0, delta=0.1)
        self.assertAlmostEqual(apply_spline(10.0, 10.0), 10.0, delta=0.1)

    def test_unknown_max_score_falls_through(self):
        """When no anchors exist for the given max_score, return raw unchanged."""
        self.assertEqual(apply_spline(3.0, 7.0), 3.0)
        self.assertEqual(apply_spline(0.0, 100.0), 0.0)

    def test_monotonicity(self):
        """Spline must be monotone within [2.0, max_score] where values > 0."""
        prev = apply_spline(2.0, 5.0)
        for raw in [3.0, 4.0, 5.0]:
            val = apply_spline(raw, 5.0)
            self.assertGreaterEqual(val, prev,
                                    f'Monotonicity broken at raw={raw}: {val} < {prev}')
            prev = val

    def test_clamped_to_range(self):
        """Spline output stays within [0, max_score] for in-range inputs."""
        for raw in [2.0, 3.0, 4.0, 5.0]:
            result = apply_spline(raw, 5.0)
            self.assertGreaterEqual(result, 0.0,
                                    f'spline({raw}) = {result} < 0')
            self.assertLessEqual(result, 5.0)


class ApplyPresetTests(TestCase):
    def test_strict_no_lift(self):
        """Strict preset should not add any lift."""
        self.assertAlmostEqual(apply_preset(3.0, 5.0, 'strict'), 3.0, delta=0.1)

    def test_lenient_adds_lift(self):
        """Lenient preset adds 12 percentage points."""
        score = apply_preset(3.0, 5.0, 'lenient')
        self.assertGreater(score, 3.0)
        self.assertLessEqual(score, 5.0)

    def test_normal_adds_sinusoidal_lift(self):
        """Normal preset adds sinusoidal lift capped at 5pp."""
        score = apply_preset(2.5, 5.0, 'normal')
        self.assertGreater(score, 2.5)  # lift at midpoint
        self.assertLessEqual(score, 5.0)

    def test_lenient_capped_at_max(self):
        """Lenient lift should not exceed max_score."""
        score = apply_preset(4.8, 5.0, 'lenient')
        self.assertLessEqual(score, 5.0)

    def test_strict_at_max_stays_max(self):
        self.assertAlmostEqual(apply_preset(5.0, 5.0, 'strict'), 5.0, delta=0.1)

    def test_unknown_strictness_defaults_to_normal(self):
        """Unknown strictness string should fall back to normal behaviour."""
        score = apply_preset(2.5, 5.0, 'unknown')
        normal_score = apply_preset(2.5, 5.0, 'normal')
        self.assertAlmostEqual(score, normal_score, delta=0.1)


class CalibrateFullPipelineTests(TestCase):
    def test_zero_in_zero_out(self):
        self.assertEqual(calibrate(0.0, 5.0, 'strict'), 0.0)

    def test_full_marks_rounds_down(self):
        """raw=5.0, max=5.0 → calibrated should be 5.0 (clamped)."""
        result = calibrate(5.0, 5.0, 'strict')
        self.assertAlmostEqual(result, 5.0, delta=0.1)

    def test_output_is_half_precision(self):
        """Every calibrated result should be a multiple of 0.5."""
        for raw in [0.0, 1.2, 2.7, 3.3, 4.9]:
            for strictness in ('strict', 'normal', 'lenient'):
                result = calibrate(raw, 5.0, strictness)
                self.assertEqual(result * 2, int(result * 2),
                                 f'{raw=} {strictness=} → {result} not 0.5 multiple')

    def test_strict_le_normal_le_lenient(self):
        """Strict ≤ Normal ≤ Lenient for the same raw score."""
        raw = 3.0
        s = calibrate(raw, 5.0, 'strict')
        n = calibrate(raw, 5.0, 'normal')
        l_ = calibrate(raw, 5.0, 'lenient')
        self.assertLessEqual(s, n)
        self.assertLessEqual(n, l_)

    def test_negative_raw_handled(self):
        """Negative raw scores may produce small positive values after spline+preset."""
        result = calibrate(-1.0, 5.0, 'strict')
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, 5.0)

    def test_overshoot_clamped_to_max(self):
        self.assertLessEqual(calibrate(10.0, 5.0, 'normal'), 5.0)

    def test_max_score_10_minimum_usable(self):
        """Basic sanity: raw=2.0 on max=10 should produce a reasonable score."""
        result = calibrate(2.0, 10.0, 'normal')
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, 10.0)
