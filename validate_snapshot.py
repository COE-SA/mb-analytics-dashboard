#!/usr/bin/env python3
"""Validate the generated static dashboard snapshot before it is published."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main(path: str) -> int:
    source = Path(path)
    if not source.is_file():
        print(f"ERROR: snapshot not found: {source}", file=sys.stderr)
        return 1

    with source.open(encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)

    errors: list[str] = []
    warnings: list[str] = []
    meta = data.get("meta", {})
    health = data.get("data_health", {})
    daily = data.get("daily_sales", [])
    monthly = data.get("monthly_sales_all", [])
    expense_breakdown = data.get("expense_breakdown")
    decision_center = data.get("decision_center")

    if int(meta.get("schema_version", 0)) < 8:
        fail(errors, "schema_version must be 8 or newer")
    if not meta.get("generated_at_iso"):
        fail(errors, "missing generated_at_iso metadata")
    if health.get("status") != "ok":
        fail(errors, "data_health.status is not ok")

    expected_daily = int((health.get("daily_sales") or {}).get("expected_days", 0))
    if expected_daily < 55:
        fail(errors, f"daily health reports only {expected_daily} days; expected at least 55")
    if len(daily) < 55:
        fail(errors, f"daily_sales contains only {len(daily)} entries; expected at least 55")
    if len(monthly) < 2:
        fail(errors, "monthly_sales_all must contain more than one month")

    if not isinstance(expense_breakdown, dict):
        fail(errors, "missing expense_breakdown")
    else:
        if not expense_breakdown.get("period_start") or not expense_breakdown.get("period_end"):
            fail(errors, "expense_breakdown is missing its period")
        if not isinstance(expense_breakdown.get("accounts"), list):
            fail(errors, "expense_breakdown.accounts must be a list")
        direct_cost = float(expense_breakdown.get("direct_cost", 0) or 0)
        operating_expense = float(expense_breakdown.get("operating_expense", 0) or 0)
        total = float(expense_breakdown.get("total", 0) or 0)
        if round(direct_cost + operating_expense - total, 2) != 0:
            fail(errors, "expense_breakdown total does not reconcile")

    if not isinstance(decision_center, dict):
        fail(errors, "missing decision_center")
    else:
        rolling = (decision_center.get("rolling_sales") or {}).get("series") or []
        heatmap = (decision_center.get("demand_timing") or {}).get("weekday_hour") or []
        if len(rolling) < 55:
            fail(errors, f"decision_center rolling series contains only {len(rolling)} rows")
        if len(heatmap) != 168:
            fail(errors, f"decision_center heatmap must contain 168 cells, got {len(heatmap)}")
        for key in ("channels", "branches", "payments", "products", "expenses", "profitability"):
            if key not in decision_center:
                fail(errors, f"decision_center is missing {key}")
        channel_rows = (decision_center.get("channels") or {}).get("rows") or []
        branch_rows = decision_center.get("branches") or []
        payment_rows = (decision_center.get("payments") or {}).get("rows") or []
        product_rows = (decision_center.get("products") or {}).get("rows") or []
        expense_rows = (decision_center.get("expenses") or {}).get("accounts") or []
        if not channel_rows:
            fail(errors, "decision_center channels are empty")
        if not branch_rows:
            fail(errors, "decision_center branches are empty")
        if not payment_rows:
            fail(errors, "decision_center payments are empty")
        if not product_rows:
            fail(errors, "decision_center products are empty")
        if not expense_rows:
            fail(errors, "decision_center expense accounts are empty")
        cost_coverage = float((decision_center.get("products") or {}).get("cost_coverage_pct", 0) or 0)
        if cost_coverage < 80:
            warnings.append(f"product standard-cost coverage is only {cost_coverage:.1f}%")

    branches = data.get("branches", {})
    if not branches.get("all_time"):
        warnings.append("no all-time branch rows were generated")
    if branches.get("all_time") == branches.get("this_month") and branches.get("all_time"):
        warnings.append("all-time branch totals equal current-month totals; inspect source data if unexpected")

    aggregators = data.get("aggregators", {})
    if aggregators.get("all_time") == aggregators.get("last_30d") and aggregators.get("all_time"):
        warnings.append("all-time application totals equal the last 30 days; inspect source data if unexpected")

    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "Snapshot validation passed: "
        f"schema={meta.get('schema_version')}, daily_rows={len(daily)}, monthly_rows={len(monthly)}."
    )
    return 0


if __name__ == "__main__":
    snapshot = sys.argv[1] if len(sys.argv) > 1 else "data.json"
    raise SystemExit(main(snapshot))
