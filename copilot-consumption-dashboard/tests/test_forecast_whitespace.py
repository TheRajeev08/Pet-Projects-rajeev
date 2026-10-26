"""Synthetic coverage for additive weekly/monthly account facts."""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("dashboard_builder", os.path.join(ROOT, "build_dashboard.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ForecastWhitespaceTests(unittest.TestCase):
    def test_missing_rows_are_unknown(self):
        account = {}
        builder.add_forecast_whitespace(account, {}, {})
        for key in ("forecast_bill", "included_pool", "forecast_above_pool", "ws_whitespace_seats", "ws_som_annual"):
            self.assertIsNone(account[key], key)

    def test_derived_values_and_zero_assigned(self):
        account = {}
        builder.add_forecast_whitespace(account, {"forecast_bill": 120, "pool": 100},
                                         {"potential_seats": 40, "assigned_seats": None, "som_annual": 500})
        self.assertEqual(account["forecast_above_pool"], 20)
        self.assertEqual(account["ws_whitespace_seats"], 40)
        builder.add_forecast_whitespace(account, {"forecast_bill": 0, "pool": 100},
                                         {"potential_seats": 10, "assigned_seats": 20})
        self.assertEqual(account["forecast_above_pool"], 0)
        self.assertEqual(account["ws_whitespace_seats"], 0)

    def test_implausible_estimates_are_withheld(self):
        account = {}
        builder.add_forecast_whitespace(account, {}, {
            "seat_estimate_source": "rejected_as_implausible", "potential_seats": 9000, "som_annual": 6000})
        for key in ("ws_potential_seats", "ws_whitespace_seats", "ws_som_annual"):
            self.assertIsNone(account[key])

    def test_join_is_owned_direct_and_deduplicated(self):
        raw = {
            "01": [{"Id": "syn-a", "Name": "Synthetic Example"}],
            "17": [{"id": "syn-a", "potential_seats": 10}, {"id": "syn-a", "potential_seats": 20},
                   {"id": "syn-unowned", "potential_seats": 900}],
        }
        model = builder.build("2030-01-07", raw, {})
        self.assertEqual(len(model["accounts"]), 1)
        self.assertEqual(model["portfolio"]["total_ws_potential_seats"], 20)
        self.assertEqual(model["portfolio"]["total_ws_whitespace_seats"], 20)
        self.assertIsNone(model["portfolio"]["total_ws_som_annual"])


if __name__ == "__main__":
    unittest.main()
