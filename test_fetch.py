from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import fetch
import validate_snapshot


class FetchHelpersTest(unittest.TestCase):
    def test_utc_boundary_uses_riyadh_business_day(self) -> None:
        self.assertEqual(fetch.utc_boundary(date(2026, 7, 21)), "2026-07-20 21:00:00")

    def test_month_key_normalizes_supported_values(self) -> None:
        self.assertEqual(fetch.month_key("July 2026"), "2026-07")
        self.assertEqual(fetch.month_key("2026-07-21"), "2026-07")
        self.assertEqual(fetch.month_key(None), None)

    def test_week_start_uses_sunday_for_ksa_reporting(self) -> None:
        self.assertEqual(fetch.week_start_sunday(date(2026, 7, 19)), date(2026, 7, 19))
        self.assertEqual(fetch.week_start_sunday(date(2026, 7, 21)), date(2026, 7, 19))
        self.assertEqual(fetch.week_start_sunday(date(2026, 7, 25)), date(2026, 7, 19))

    def test_month_forecast_uses_actual_number_of_days(self) -> None:
        daily = [
            {"day": "2026-04-01", "revenue": 100, "orders": 2},
            {"day": "2026-04-02", "revenue": 100, "orders": 2},
            {"day": "2026-04-03", "revenue": 100, "orders": 2},
        ]
        result = fetch.forecast_month(daily, date(2026, 4, 4))
        self.assertIsNotNone(result)
        self.assertEqual(result["days_in_month"], 30)
        self.assertEqual(result["days_elapsed"], 3)
        self.assertEqual(result["projected_month_revenue"], 3000)

    def test_next_day_forecast_prefers_matching_weekday_when_available(self) -> None:
        daily = [
            {"day": "2026-06-01", "revenue": 100, "orders": 10},
            {"day": "2026-06-08", "revenue": 200, "orders": 20},
            {"day": "2026-06-15", "revenue": 300, "orders": 30},
            {"day": "2026-06-22", "revenue": 400, "orders": 40},
        ]
        result = fetch.forecast_next_day(daily, date(2026, 6, 28))
        self.assertIsNotNone(result)
        self.assertEqual(result["method"], "same_weekday_recent_history")
        self.assertEqual(result["sample_size"], 4)
        self.assertEqual(result["revenue"], 250)
        self.assertEqual(result["orders"], 25)

    def test_year_forecast_uses_closed_days_only(self) -> None:
        result = fetch.forecast_year({"revenue": 18_100}, date(2026, 7, 2))
        self.assertIsNotNone(result)
        self.assertEqual(result["days_elapsed"], 182)
        self.assertEqual(result["days_in_year"], 365)
        self.assertEqual(result["projected_year_revenue"], 36_299.45)

    def test_complete_snapshot_passes_release_validation(self) -> None:
        snapshot = {
            "meta": {"schema_version": 6, "generated_at_iso": "2026-07-21T06:00:00+03:00"},
            "data_health": {"status": "ok", "daily_sales": {"expected_days": 60}},
            "daily_sales": [{"day": f"2026-06-{(index % 30) + 1:02d}"} for index in range(60)],
            "monthly_sales_all": [{"month": "2026-07"}, {"month": "2026-06"}],
            "branches": {"all_time": {"A": {"revenue": 100}}, "this_month": {"A": {"revenue": 10}}},
            "aggregators": {"all_time": {"App": {"revenue": 100}}, "last_30d": {"App": {"revenue": 10}}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            self.assertEqual(validate_snapshot.main(str(path)), 0)


if __name__ == "__main__":
    unittest.main()
