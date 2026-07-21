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

    if int(meta.get("schema_version", 0)) < 5:
        fail(errors, "schema_version must be 5 or newer")
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
