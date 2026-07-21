from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from unittest.mock import patch
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

    @patch("fetch.search_count")
    @patch("fetch.read_group_all")
    def test_branch_breakdown_keeps_duplicate_unnamed_configs(self, read_group_mock, search_count_mock) -> None:
        read_group_mock.return_value = [
            {"config_id": [11, "not used"], "amount_total": 1_000},
            {"config_id": [22, "not used"], "amount_total": 2_000},
        ]
        search_count_mock.side_effect = [10, 20]
        result = fetch.pos_by_branch()
        self.assertEqual(len(result), 2)
        self.assertIn("فرع غير مسمى #11", result)
        self.assertIn("فرع غير مسمى #22", result)
        self.assertEqual(sum(item["revenue"] for item in result.values()), 3_000)
        self.assertEqual(sum(item["orders"] for item in result.values()), 30)

    @patch("fetch.read_group_all")
    def test_expense_breakdown_reconciles_direct_and_operating_costs(self, read_group_mock) -> None:
        read_group_mock.side_effect = [
            [{"account_id": [10, "مواد خام"], "debit": 1_000, "credit": 50}],
            [
                {"account_id": [20, "إيجار"], "debit": 500, "credit": 0},
                {"account_id": [30, "تسوية"], "debit": 100, "credit": 100},
            ],
        ]
        result = fetch.expense_breakdown(date(2026, 1, 1), date(2026, 7, 21))
        self.assertEqual(result["direct_cost"], 950)
        self.assertEqual(result["operating_expense"], 500)
        self.assertEqual(result["total"], 1_450)
        self.assertEqual(len(result["accounts"]), 2)
        self.assertEqual(result["accounts"][0]["classification"], "direct_cost")
        self.assertEqual(result["accounts"][1]["classification"], "operating_expense")

    def test_rolling_sales_metrics_compares_complete_windows(self) -> None:
        daily = [
            {"day": f"2026-06-{(index % 30) + 1:02d}", "revenue": 100 + index, "orders": 10}
            for index in range(56)
        ]
        result = fetch.rolling_sales_metrics(daily)
        self.assertEqual(len(result["series"]), 56)
        self.assertIsNotNone(result["series"][-1]["avg_28_revenue"])
        self.assertIsNotNone(result["last_7_vs_prior_7"]["revenue_change_pct"])
        self.assertIsNotNone(result["last_28_vs_prior_28"]["revenue_change_pct"])
        self.assertIsNotNone(result["revenue_acceleration_pp"])

    def test_direct_pos_customer_is_not_classified_as_platform(self) -> None:
        self.assertIsNone(fetch.aggregator_name("POS Customer (POSZ) ***"))
        self.assertEqual(fetch.aggregator_name("Keeta(POSZ) ***"), "Keeta")

    def test_channel_portfolio_adds_direct_residual_and_concentration(self) -> None:
        result = fetch.channel_portfolio(
            {"Platform A": {"revenue": 300, "orders": 3}, "Platform B": {"revenue": 200, "orders": 4}},
            {"revenue": 1_000, "orders": 20},
        )
        self.assertEqual(result["platform_share_pct"], 50.0)
        self.assertEqual(result["rows"][0]["channel"], "نقاط البيع المباشرة")
        self.assertEqual(result["rows"][0]["revenue"], 500)
        self.assertEqual(result["top_3_share_pct"], 100.0)

    def test_product_portfolio_flags_missing_standard_cost(self) -> None:
        result = fetch.product_portfolio(
            [
                {"product": "A", "category": "Cake", "qty": 2, "revenue": 200, "cogs": 100, "gross_profit": 100},
                {"product": "B", "category": "Service", "qty": 1, "revenue": 50, "cogs": 0, "gross_profit": 50},
            ]
        )
        self.assertEqual(result["cost_gap_count"], 1)
        self.assertEqual(result["cost_coverage_pct"], 50.0)
        self.assertEqual(len(result["categories"]), 2)

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
            "meta": {"schema_version": 8, "generated_at_iso": "2026-07-21T06:00:00+03:00"},
            "data_health": {"status": "ok", "daily_sales": {"expected_days": 60}},
            "daily_sales": [{"day": f"2026-06-{(index % 30) + 1:02d}"} for index in range(60)],
            "monthly_sales_all": [{"month": "2026-07"}, {"month": "2026-06"}],
            "branches": {"all_time": {"A": {"revenue": 100}}, "this_month": {"A": {"revenue": 10}}},
            "aggregators": {"all_time": {"App": {"revenue": 100}}, "last_30d": {"App": {"revenue": 10}}},
            "expense_breakdown": {
                "period_start": "2026-01-01",
                "period_end": "2026-07-20",
                "direct_cost": 100,
                "operating_expense": 25,
                "total": 125,
                "accounts": [{"account": "مواد خام", "amount": 100}],
            },
            "decision_center": {
                "rolling_sales": {"series": [{"day": "2026-06-01"} for _ in range(60)]},
                "demand_timing": {"weekday_hour": [{"weekday": day, "hour": hour} for day in range(7) for hour in range(24)]},
                "channels": {"rows": [{"channel": "POS", "revenue": 100}]},
                "branches": [{"branch": "A", "revenue": 100}],
                "payments": {"rows": [{"method": "Cash", "amount": 100}]},
                "products": {"rows": [{"product": "A", "revenue": 100}], "cost_coverage_pct": 100},
                "expenses": {"accounts": [{"account": "مواد خام", "amount": 100}]},
                "profitability": {"weighted_margin_pct": 20},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            self.assertEqual(validate_snapshot.main(str(path)), 0)


if __name__ == "__main__":
    unittest.main()
