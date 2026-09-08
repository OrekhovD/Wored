"""Input and compatibility regressions that do not require infrastructure."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT / "chatbot")]
from forecast_input import parse_forecast_input
from services.execution_engine import calc_liquidation_price, validate_leverage
from services.sim_math import liquidation_price, settlement


class ForecastInputTests(unittest.TestCase):
    def test_hours_convert_to_actual_period_steps(self):
        result = parse_forecast_input({"symbol": " BTCUSDT ", "horizon_hours": 4, "base_timeframe": "15min"})
        self.assertEqual((result.symbol, result.horizon_steps), ("btcusdt", 16))

    def test_ambiguous_and_nonintegral_horizons_fail(self):
        for payload in ({"horizon_steps": 4, "horizon_hours": 4, "base_timeframe": "15min"},
                        {"horizon_hours": 1, "base_timeframe": "4hour"},
                        {"horizon_steps": True, "horizon_hours": 1},
                        {"horizon_steps": "4"}, {"horizon_hours": 1.5}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_forecast_input(payload)

    def test_bounds_and_malformed_inputs_fail(self):
        for payload in ([], {"symbol": None}, {"symbol": " "}, {"base_timeframe": []},
                        {"horizon_steps": 0}, {"horizon_steps": 49}, {"depth": True},
                        {"depth": 0}, {"depth": 11}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_forecast_input(payload)

    def test_defaults_and_matching_legacy_fields(self):
        self.assertEqual(parse_forecast_input({}).horizon_steps, 4)
        self.assertEqual(parse_forecast_input({"horizon_hours": 2, "horizon_steps": 8,
                                              "base_timeframe": "15min"}).horizon_steps, 8)


class CalculationCompatibilityTests(unittest.TestCase):
    def test_existing_simulator_positions_keep_legacy_liquidation(self):
        self.assertAlmostEqual(liquidation_price(100, 100, "long", 1), 99.5)
        self.assertAlmostEqual(liquidation_price(100, 100, "short", 1), 100.5)

    def test_existing_session_positions_keep_their_distinct_legacy_rule(self):
        self.assertAlmostEqual(calc_liquidation_price(100, 100, "long", 1), 98.5)
        self.assertAlmostEqual(calc_liquidation_price(100, 100, "short", 1), 101.5)

    def test_new_positions_share_same_liquidation_equation(self):
        for direction in ("long", "short"):
            value = liquidation_price(100, 50, direction)
            self.assertAlmostEqual(calc_liquidation_price(100, 50, direction), value, places=7)
            gross = (value - 100) * (1 if direction == "long" else -1)
            self.assertAlmostEqual(2 + gross - .06, value * .005)

    def test_old_and_new_closing_fee_are_explicit(self):
        self.assertAlmostEqual(settlement("long", 100, 110, 2, .12, .03, 1)[0], 19.73)
        pnl, fee = settlement("long", 100, 110, 2, .12, .03, 2)
        self.assertAlmostEqual(fee, .132)
        self.assertAlmostEqual(pnl, 19.718)

    def test_session_leverage_rejects_unsafe_and_coerced_values(self):
        for leverage in (True, 10.0, "10", 0, 125, 200):
            self.assertFalse(validate_leverage(leverage))
        for leverage in (10, 25, 50, 100):
            self.assertTrue(validate_leverage(leverage))
