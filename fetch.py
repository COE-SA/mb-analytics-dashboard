#!/usr/bin/env python3
"""Munch Bakery analytics extract, transform, and publish script.

The script reads Odoo credentials exclusively from environment variables. It intentionally
contains no credential defaults and writes a static ``data.json`` snapshot for the dashboard.
"""
from __future__ import annotations

import calendar
import json
import os
import sys
import xmlrpc.client
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

KSA = timezone(timedelta(hours=3))
UTC = timezone.utc
HISTORY_START = date(2024, 1, 1)

ODOO_URL = ""
ODOO_DB = ""
ODOO_USER = ""
ODOO_KEY = ""
OUT_FILE = "data.json"
COMMON: xmlrpc.client.ServerProxy | None = None
MODELS: xmlrpc.client.ServerProxy | None = None
UID: int | None = None


# ─── Configuration and transport ──────────────────────────────────────────────
def log(message: str) -> None:
    print(f"[{datetime.now(KSA).strftime('%H:%M:%S')}] {message}", flush=True)


def configure() -> None:
    """Load required settings without leaking any sensitive values."""
    global ODOO_URL, ODOO_DB, ODOO_USER, ODOO_KEY, OUT_FILE
    required = ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Configure them as repository secrets; never hard-code credentials."
        )

    ODOO_URL = os.environ["ODOO_URL"].rstrip("/")
    ODOO_DB = os.environ["ODOO_DB"]
    ODOO_USER = os.environ["ODOO_USER"]
    ODOO_KEY = os.environ["ODOO_API_KEY"]
    OUT_FILE = os.environ.get("OUTPUT_FILE", "data.json")


def connect() -> None:
    """Authenticate once and initialise XML-RPC endpoints."""
    global COMMON, MODELS, UID
    log("Connecting to Odoo…")
    COMMON = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    MODELS = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    UID = COMMON.authenticate(ODOO_DB, ODOO_USER, ODOO_KEY, {})
    if not UID:
        raise SystemExit("Odoo authentication failed. Verify repository secrets and service-account access.")
    log("Odoo authentication succeeded.")


def execute(model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
    if MODELS is None or UID is None:
        raise RuntimeError("Odoo is not connected")
    return MODELS.execute_kw(ODOO_DB, UID, ODOO_KEY, model, method, args, kwargs or {})


def search_count(model: str, domain: list[list[Any]]) -> int:
    return int(execute(model, "search_count", [domain]))


def search_read_all(
    model: str,
    domain: list[list[Any]],
    fields: list[str],
    *,
    order: str = "",
    page_size: int = 5_000,
) -> list[dict[str, Any]]:
    """Fetch every matching row in pages instead of silently accepting a server limit."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        kwargs: dict[str, Any] = {"fields": fields, "limit": page_size, "offset": offset}
        if order:
            kwargs["order"] = order
        batch = execute(model, "search_read", [domain], kwargs) or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        offset += page_size


def read_group_all(
    model: str,
    domain: list[list[Any]],
    fields: list[str],
    groupby: list[str],
    *,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Return all group rows, including groups beyond Odoo's default limit."""
    groups: list[dict[str, Any]] = []
    offset = 0
    while True:
        kwargs = {
            "fields": fields,
            "groupby": groupby,
            "limit": page_size,
            "offset": offset,
            "lazy": False,
        }
        batch = execute(model, "read_group", [domain], kwargs) or []
        groups.extend(batch)
        if len(batch) < page_size:
            return groups
        offset += page_size


# ─── Time and value helpers ───────────────────────────────────────────────────
def utc_boundary(value: date) -> str:
    """Return the UTC instant representing 00:00 at the start of a KSA business day."""
    return datetime.combine(value, time.min, tzinfo=KSA).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def day_domain(field: str, start: date, end_exclusive: date) -> list[list[Any]]:
    return [[field, ">=", utc_boundary(start)], [field, "<", utc_boundary(end_exclusive)]]


def pos_domain(extra: Iterable[list[Any]] | None = None) -> list[list[Any]]:
    return [["state", "!=", "cancel"]] + list(extra or [])


def parse_odoo_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def ksa_date(value: str | None) -> date | None:
    parsed = parse_odoo_datetime(value)
    return parsed.astimezone(KSA).date() if parsed else None


def ksa_hour(value: str | None) -> int | None:
    parsed = parse_odoo_datetime(value)
    return parsed.astimezone(KSA).hour if parsed else None


def date_sequence(start: date, end_exclusive: date) -> list[date]:
    result: list[date] = []
    cursor = start
    while cursor < end_exclusive:
        result.append(cursor)
        cursor += timedelta(days=1)
    return result


def month_key(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value)
    if len(raw) >= 7 and raw[4] == "-":
        return raw[:7]
    for pattern in ("%B %Y", "%b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, pattern).strftime("%Y-%m")
        except ValueError:
            pass
    return raw[:7] if len(raw) >= 7 else None


def many2one(value: Any, fallback: str = "غير محدد") -> tuple[int | None, str]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return value[0], str(value[1])
    return None, fallback


def short_branch_name(name: str) -> str:
    return name.split("(")[-1].replace(")", "").strip() if "(" in name else name


def add_months(first_of_month: date, months: int) -> date:
    ordinal = first_of_month.year * 12 + first_of_month.month - 1 + months
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def week_start_sunday(value: date) -> date:
    """Return the Sunday that begins the KSA reporting week for a given date."""
    return value - timedelta(days=(value.weekday() + 1) % 7)


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def percent_change(current: Any, baseline: Any) -> float | None:
    base = number(baseline)
    if base == 0:
        return None
    return round((number(current) - base) / base * 100, 1)


def sales_summary(rows: Iterable[dict[str, Any]]) -> dict[str, float | int]:
    values = list(rows)
    revenue = round(sum(number(item.get("revenue")) for item in values), 2)
    orders = sum(int(number(item.get("orders"))) for item in values)
    return {
        "revenue": revenue,
        "orders": orders,
        "aov": round(revenue / orders, 2) if orders else 0.0,
    }


def comparison_summary(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "current": current,
        "baseline": baseline,
        "revenue_change_pct": percent_change(current.get("revenue"), baseline.get("revenue")),
        "orders_change_pct": percent_change(current.get("orders"), baseline.get("orders")),
        "aov_change_pct": percent_change(current.get("aov"), baseline.get("aov")),
    }


def rolling_sales_metrics(daily_sales: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(daily_sales, key=lambda item: str(item.get("day") or ""))
    rolling: list[dict[str, Any]] = []
    for index, row in enumerate(ordered):
        item: dict[str, Any] = {
            "day": row.get("day"),
            "revenue": round(number(row.get("revenue")), 2),
            "orders": int(number(row.get("orders"))),
        }
        for window in (7, 28):
            if index + 1 < window:
                item[f"avg_{window}_revenue"] = None
                item[f"avg_{window}_orders"] = None
                continue
            sample = ordered[index - window + 1:index + 1]
            summary = sales_summary(sample)
            item[f"avg_{window}_revenue"] = round(number(summary["revenue"]) / window, 2)
            item[f"avg_{window}_orders"] = round(number(summary["orders"]) / window, 2)
        rolling.append(item)

    current_7 = sales_summary(ordered[-7:]) if len(ordered) >= 7 else sales_summary([])
    prior_7 = sales_summary(ordered[-14:-7]) if len(ordered) >= 14 else sales_summary([])
    current_28 = sales_summary(ordered[-28:]) if len(ordered) >= 28 else sales_summary([])
    prior_28 = sales_summary(ordered[-56:-28]) if len(ordered) >= 56 else sales_summary([])
    seven = comparison_summary(current_7, prior_7)
    twenty_eight = comparison_summary(current_28, prior_28)
    acceleration = None
    if seven["revenue_change_pct"] is not None and twenty_eight["revenue_change_pct"] is not None:
        acceleration = round(number(seven["revenue_change_pct"]) - number(twenty_eight["revenue_change_pct"]), 1)
    return {
        "series": rolling,
        "last_7_vs_prior_7": seven,
        "last_28_vs_prior_28": twenty_eight,
        "revenue_acceleration_pp": acceleration,
    }


def enrich_share_rows(
    rows: list[dict[str, Any]],
    value_key: str,
    *,
    total: float | None = None,
    absolute: bool = False,
) -> list[dict[str, Any]]:
    measure = lambda item: abs(number(item.get(value_key))) if absolute else number(item.get(value_key))
    denominator = abs(number(total)) if total is not None else sum(measure(item) for item in rows)
    cumulative = 0.0
    enriched: list[dict[str, Any]] = []
    for row in sorted(rows, key=measure, reverse=True):
        share = round(measure(row) / denominator * 100, 1) if denominator > 0 else 0.0
        cumulative = round(cumulative + share, 1)
        enriched.append({**row, "share_pct": share, "cumulative_share_pct": min(cumulative, 100.0)})
    return enriched


def channel_portfolio(platforms: dict[str, dict[str, Any]], total_sales: dict[str, Any]) -> dict[str, Any]:
    total_revenue = number(total_sales.get("revenue"))
    total_orders = int(number(total_sales.get("orders")))
    platform_revenue = sum(number(item.get("revenue")) for item in platforms.values())
    platform_orders = sum(int(number(item.get("orders"))) for item in platforms.values())
    rows = [
        {
            "channel": name,
            "channel_type": "platform",
            "revenue": round(number(value.get("revenue")), 2),
            "orders": int(number(value.get("orders"))),
            "aov": round(number(value.get("revenue")) / number(value.get("orders")), 2) if number(value.get("orders")) > 0 else 0.0,
        }
        for name, value in platforms.items()
    ]
    direct_revenue = max(0.0, total_revenue - platform_revenue)
    direct_orders = max(0, total_orders - platform_orders)
    rows.append(
        {
            "channel": "نقاط البيع المباشرة",
            "channel_type": "direct",
            "revenue": round(direct_revenue, 2),
            "orders": direct_orders,
            "aov": round(direct_revenue / direct_orders, 2) if direct_orders else 0.0,
        }
    )
    enriched = enrich_share_rows(rows, "revenue", total=total_revenue)
    top_shares = [number(item.get("share_pct")) for item in enriched]
    return {
        "total_revenue": round(total_revenue, 2),
        "platform_revenue": round(platform_revenue, 2),
        "platform_share_pct": round(platform_revenue / total_revenue * 100, 1) if total_revenue else 0.0,
        "top_1_share_pct": round(sum(top_shares[:1]), 1),
        "top_3_share_pct": round(sum(top_shares[:3]), 1),
        "rows": enriched,
    }


def branch_portfolio(branches: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    total_revenue = sum(number(value.get("revenue")) for value in branches.values())
    rows = [
        {
            "branch": name,
            "revenue": round(number(value.get("revenue")), 2),
            "orders": int(number(value.get("orders"))),
            "aov": round(number(value.get("revenue")) / number(value.get("orders")), 2) if number(value.get("orders")) > 0 else 0.0,
        }
        for name, value in branches.items()
    ]
    return enrich_share_rows(rows, "revenue", total=total_revenue)


def payment_portfolio(rows: list[dict[str, Any]]) -> dict[str, Any]:
    enriched = enrich_share_rows(
        [
            {
                **row,
                "aov": round(number(row.get("amount")) / number(row.get("count")), 2) if number(row.get("count")) > 0 else 0.0,
            }
            for row in rows
        ],
        "amount",
    )
    cash = sum(
        number(item.get("amount"))
        for item in enriched
        if any(token in str(item.get("method") or "").lower() for token in ("cash", "نقد"))
    )
    total = sum(number(item.get("amount")) for item in enriched)
    return {
        "total": round(total, 2),
        "cash_amount": round(cash, 2),
        "cash_share_pct": round(cash / total * 100, 1) if total else 0.0,
        "rows": enriched,
    }


def product_portfolio(rows: list[dict[str, Any]]) -> dict[str, Any]:
    revenue_total = sum(number(item.get("revenue")) for item in rows)
    cost_gaps = sum(1 for item in rows if number(item.get("cogs")) <= 0 and number(item.get("qty")) > 0)
    enriched = enrich_share_rows(rows, "revenue", total=revenue_total)
    categories: dict[str, dict[str, float]] = defaultdict(lambda: {"revenue": 0.0, "qty": 0.0, "gross_profit": 0.0})
    for item in rows:
        category = str(item.get("category") or "غير محدد")
        categories[category]["revenue"] += number(item.get("revenue"))
        categories[category]["qty"] += number(item.get("qty"))
        categories[category]["gross_profit"] += number(item.get("gross_profit"))
    category_rows = [
        {
            "category": name,
            "revenue": round(value["revenue"], 2),
            "qty": round(value["qty"], 2),
            "gross_profit": round(value["gross_profit"], 2),
        }
        for name, value in categories.items()
    ]
    return {
        "rows": enriched,
        "categories": enrich_share_rows(category_rows, "revenue"),
        "cost_gap_count": cost_gaps,
        "cost_coverage_pct": round((len(rows) - cost_gaps) / len(rows) * 100, 1) if rows else 0.0,
    }


def expense_pareto(expense_structure: dict[str, Any]) -> dict[str, Any]:
    accounts = enrich_share_rows(
        list(expense_structure.get("accounts") or []),
        "amount",
        total=abs(number(expense_structure.get("total"))),
        absolute=True,
    )
    return {
        "accounts": accounts,
        "top_3_share_pct": round(sum(number(item.get("share_pct")) for item in accounts[:3]), 1),
        "top_10_share_pct": round(sum(number(item.get("share_pct")) for item in accounts[:10]), 1),
    }


def profitability_quality(pl_rows: list[dict[str, Any]]) -> dict[str, Any]:
    recent = pl_rows[-12:]
    revenues = sum(number(item.get("revenue")) for item in recent)
    expenses = sum(number(item.get("expenses")) for item in recent)
    profits = sum(number(item.get("gross_profit")) for item in recent)
    margins = [number(item.get("margin_pct")) for item in recent]
    average_margin = round(sum(margins) / len(margins), 1) if margins else 0.0
    variance = sum((margin - average_margin) ** 2 for margin in margins) / len(margins) if margins else 0.0
    return {
        "revenue": round(revenues, 2),
        "expenses": round(expenses, 2),
        "operating_result": round(profits, 2),
        "expense_ratio_pct": round(expenses / revenues * 100, 1) if revenues else 0.0,
        "weighted_margin_pct": round(profits / revenues * 100, 1) if revenues else 0.0,
        "average_monthly_margin_pct": average_margin,
        "margin_volatility_pp": round(variance ** 0.5, 1),
        "negative_months": [item.get("month") for item in recent if number(item.get("gross_profit")) < 0],
        "series": recent,
    }


# ─── Core sales aggregations ───────────────────────────────────────────────────
def pos_total(extra: Iterable[list[Any]] | None = None) -> dict[str, float | int]:
    domain = pos_domain(extra)
    grouped = read_group_all("pos.order", domain, ["amount_total"], [])
    revenue = round(number(grouped[0].get("amount_total")) if grouped else 0.0, 2)
    return {"revenue": revenue, "orders": search_count("pos.order", domain)}


def pos_daily_range(start: date, end_exclusive: date) -> list[dict[str, Any]]:
    """Aggregate a complete KSA-local daily series, including zero-sales days."""
    domain = pos_domain(day_domain("date_order", start, end_exclusive))
    rows = search_read_all("pos.order", domain, ["date_order", "amount_total"], order="date_order asc")
    totals: dict[date, dict[str, float | int]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0})
    for row in rows:
        row_day = ksa_date(row.get("date_order"))
        if row_day is None or not start <= row_day < end_exclusive:
            continue
        totals[row_day]["revenue"] = round(number(totals[row_day]["revenue"]) + number(row.get("amount_total")), 2)
        totals[row_day]["orders"] = int(totals[row_day]["orders"]) + 1

    return [
        {
            "day": item.isoformat(),
            "revenue": round(number(totals[item]["revenue"]), 2),
            "orders": int(totals[item]["orders"]),
        }
        for item in date_sequence(start, end_exclusive)
    ]


def pos_hourly_range(start: date, end_exclusive: date) -> list[dict[str, Any]]:
    domain = pos_domain(day_domain("date_order", start, end_exclusive))
    rows = search_read_all("pos.order", domain, ["date_order", "amount_total"])
    totals: dict[int, dict[str, float | int]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0})
    for row in rows:
        hour = ksa_hour(row.get("date_order"))
        if hour is None:
            continue
        totals[hour]["revenue"] = round(number(totals[hour]["revenue"]) + number(row.get("amount_total")), 2)
        totals[hour]["orders"] = int(totals[hour]["orders"]) + 1

    return [
        {"hour": hour, "revenue": round(number(totals[hour]["revenue"]), 2), "orders": int(totals[hour]["orders"])}
        for hour in range(24)
    ]


def pos_weekday_hour_range(start: date, end_exclusive: date) -> list[dict[str, Any]]:
    """Aggregate orders by KSA weekday and hour for operational heatmaps."""
    domain = pos_domain(day_domain("date_order", start, end_exclusive))
    rows = search_read_all("pos.order", domain, ["date_order", "amount_total"])
    totals: dict[tuple[int, int], dict[str, float | int]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0})
    for row in rows:
        parsed = parse_odoo_datetime(row.get("date_order"))
        if parsed is None:
            continue
        local = parsed.astimezone(KSA)
        key = (local.weekday(), local.hour)
        totals[key]["revenue"] = round(number(totals[key]["revenue"]) + number(row.get("amount_total")), 2)
        totals[key]["orders"] = int(totals[key]["orders"]) + 1
    weekday_labels = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    result: list[dict[str, Any]] = []
    for weekday in range(7):
        for hour in range(24):
            value = totals[(weekday, hour)]
            orders = int(value["orders"])
            revenue = round(number(value["revenue"]), 2)
            result.append(
                {
                    "weekday": weekday,
                    "weekday_label": weekday_labels[weekday],
                    "hour": hour,
                    "revenue": revenue,
                    "orders": orders,
                    "aov": round(revenue / orders, 2) if orders else 0.0,
                }
            )
    return result


def pos_monthly_range(start: date, end_exclusive: date) -> list[dict[str, Any]]:
    """Aggregate every order page into monthly totals without a fixed record cap."""
    domain = pos_domain(day_domain("date_order", start, end_exclusive))
    rows = search_read_all("pos.order", domain, ["date_order", "amount_total"], order="date_order asc")
    totals: dict[str, dict[str, float | int]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0})
    for row in rows:
        row_day = ksa_date(row.get("date_order"))
        if row_day is None:
            continue
        key = row_day.strftime("%Y-%m")
        totals[key]["revenue"] = round(number(totals[key]["revenue"]) + number(row.get("amount_total")), 2)
        totals[key]["orders"] = int(totals[key]["orders"]) + 1
    return [
        {"month": key, "revenue": round(number(value["revenue"]), 2), "orders": int(value["orders"])}
        for key, value in sorted(totals.items())
    ]


def pos_by_branch(extra: Iterable[list[Any]] | None = None) -> dict[str, dict[str, float | int]]:
    domain = pos_domain(extra)
    groups = read_group_all("pos.order", domain, ["amount_total"], ["config_id"])
    result: dict[str, dict[str, float | int]] = {}
    for group in groups:
        config_id, config_name = many2one(group.get("config_id"), "غير محدد")
        branch_name = short_branch_name(config_name)
        group_domain = domain + [["config_id", "=", config_id]] if config_id else domain + [["config_id", "=", False]]
        result[branch_name] = {
            "revenue": round(number(group.get("amount_total")), 2),
            "orders": search_count("pos.order", group_domain),
        }
    return result


AGGREGATORS = {
    "Hunger Station (POSZ) ***": "Hunger Station",
    "Keeta(POSZ) ***": "Keeta",
    "Taker Website(POSZ) ***": "Taker Website",
    "ToYou (POSZ) ***": "ToYou",
    "Ninja(POSZ) ***": "Ninja",
    "Marsool (POSZ) ***": "Marsool",
    "JAHEZ (POSZ) ***": "JAHEZ",
    "The Chefz (POSZ) ***": "The Chefz",
    "Noon (POSZ) ***": "Noon",
    "Careem (POSZ) ***": "Careem",
    "Mr.Mandoob (POSZ) ***": "Mr.Mandoob",
    "COE Marketing (POSZ) ***": "COE Marketing",
    "Tamara (POSZ) ***": "Tamara",
}


def aggregator_name(raw_name: str) -> str | None:
    if raw_name in AGGREGATORS:
        return AGGREGATORS[raw_name]
    if "POSZ" not in raw_name:
        return None
    normalized = raw_name.replace(" (POSZ) ***", "").replace("(POSZ) ***", "").strip()
    direct_labels = {"pos customer", "walk-in customer", "walk in customer", "general customer", "عميل نقاط البيع"}
    if normalized.casefold() in direct_labels:
        return None
    return normalized


def pos_by_aggregator(extra: Iterable[list[Any]] | None = None) -> dict[str, dict[str, float | int]]:
    domain = pos_domain([["partner_id", "!=", False]] + list(extra or []))
    groups = read_group_all("pos.order", domain, ["amount_total"], ["partner_id"])
    result: dict[str, dict[str, float | int]] = {}
    for group in groups:
        partner_id, partner_name = many2one(group.get("partner_id"), "")
        display_name = aggregator_name(partner_name)
        if not display_name:
            continue
        group_domain = domain + [["partner_id", "=", partner_id]] if partner_id else domain + [["partner_id", "=", False]]
        result[display_name] = {
            "revenue": round(number(group.get("amount_total")), 2),
            "orders": search_count("pos.order", group_domain),
        }
    return result


def payment_breakdown(extra: Iterable[list[Any]] | None = None) -> list[dict[str, Any]]:
    domain = list(extra or [])
    groups = read_group_all("pos.payment", domain, ["amount"], ["payment_method_id"])
    result: list[dict[str, Any]] = []
    for group in groups:
        method_id, method_name = many2one(group.get("payment_method_id"), "غير محدد")
        group_domain = domain + [["payment_method_id", "=", method_id]] if method_id else domain + [["payment_method_id", "=", False]]
        result.append(
            {
                "method": method_name,
                "amount": round(number(group.get("amount")), 2),
                "count": search_count("pos.payment", group_domain),
            }
        )
    return sorted(result, key=lambda item: item["amount"], reverse=True)


def top_products(extra: Iterable[list[Any]] | None = None, limit: int = 30) -> list[dict[str, Any]]:
    domain = [["order_id.state", "!=", "cancel"]] + list(extra or [])
    groups = read_group_all("pos.order.line", domain, ["qty", "price_subtotal_incl"], ["product_id"])
    ranked = sorted(groups, key=lambda item: number(item.get("price_subtotal_incl")), reverse=True)[:limit]
    product_ids = [many2one(item.get("product_id"))[0] for item in ranked]
    product_ids = [item for item in product_ids if item is not None]
    product_rows = search_read_all(
        "product.product",
        [["id", "in", product_ids]],
        ["id", "standard_price", "categ_id"],
    ) if product_ids else []
    products = {row["id"]: row for row in product_rows}

    result: list[dict[str, Any]] = []
    for group in ranked:
        product_id, product_name = many2one(group.get("product_id"), "غير محدد")
        product = products.get(product_id or -1, {})
        _, category = many2one(product.get("categ_id"), "غير محدد")
        quantity = number(group.get("qty"))
        revenue = number(group.get("price_subtotal_incl"))
        standard_cost = number(product.get("standard_price"))
        estimated_cogs = round(standard_cost * quantity, 2)
        estimated_profit = round(revenue - estimated_cogs, 2)
        result.append(
            {
                "product": product_name,
                "category": category,
                "qty": round(quantity, 2),
                "revenue": round(revenue, 2),
                "cogs": estimated_cogs,
                "gross_profit": estimated_profit,
                "margin_pct": round(estimated_profit / revenue * 100, 1) if revenue > 0 else 0,
                "cost_basis": "current_standard_cost",
            }
        )
    return result


# ─── Financial and operational aggregations ───────────────────────────────────
def pl_monthly(start: date) -> list[dict[str, Any]]:
    revenue_rows = read_group_all(
        "account.move.line",
        [
            ["date", ">=", start.isoformat()],
            ["account_id.account_type", "in", ["income", "income_other"]],
            ["move_id.state", "=", "posted"],
        ],
        ["credit", "debit"],
        ["date:month"],
    )
    expense_rows = read_group_all(
        "account.move.line",
        [
            ["date", ">=", start.isoformat()],
            ["account_id.account_type", "in", ["expense", "expense_direct_cost"]],
            ["move_id.state", "=", "posted"],
        ],
        ["debit", "credit"],
        ["date:month"],
    )
    revenue = {
        month_key(item.get("date:month")): round(number(item.get("credit")) - number(item.get("debit")), 2)
        for item in revenue_rows
        if month_key(item.get("date:month"))
    }
    expenses = {
        month_key(item.get("date:month")): round(number(item.get("debit")) - number(item.get("credit")), 2)
        for item in expense_rows
        if month_key(item.get("date:month"))
    }
    result: list[dict[str, Any]] = []
    for key in sorted(set(revenue) | set(expenses)):
        value_revenue = revenue.get(key, 0.0)
        value_expenses = expenses.get(key, 0.0)
        profit = round(value_revenue - value_expenses, 2)
        result.append(
            {
                "month": key,
                "revenue": value_revenue,
                "expenses": value_expenses,
                "gross_profit": profit,
                "margin_pct": round(profit / value_revenue * 100, 1) if value_revenue > 0 else 0,
            }
        )
    return result


def expense_breakdown(start: date, end_exclusive: date) -> dict[str, Any]:
    """Return accounting-expense accounts split between direct cost and operating expense."""
    categories = (
        ("direct_cost", "تكاليف مباشرة", "expense_direct_cost"),
        ("operating_expense", "مصروفات تشغيلية", "expense"),
    )
    accounts: list[dict[str, Any]] = []
    totals = {"direct_cost": 0.0, "operating_expense": 0.0}
    for classification, classification_label, account_type in categories:
        grouped = read_group_all(
            "account.move.line",
            [
                ["date", ">=", start.isoformat()],
                ["date", "<", end_exclusive.isoformat()],
                ["move_id.state", "=", "posted"],
                ["account_id.account_type", "=", account_type],
            ],
            ["debit", "credit"],
            ["account_id"],
        )
        for item in grouped:
            account_id, account_name = many2one(item.get("account_id"), "غير محدد")
            if account_id is None:
                continue
            amount = round(number(item.get("debit")) - number(item.get("credit")), 2)
            if amount == 0:
                continue
            totals[classification] = round(totals[classification] + amount, 2)
            accounts.append(
                {
                    "account_id": account_id,
                    "account": account_name,
                    "classification": classification,
                    "classification_label": classification_label,
                    "amount": amount,
                }
            )
    accounts.sort(key=lambda item: abs(number(item["amount"])), reverse=True)
    return {
        "period_start": start.isoformat(),
        "period_end": (end_exclusive - timedelta(days=1)).isoformat(),
        "direct_cost": round(totals["direct_cost"], 2),
        "operating_expense": round(totals["operating_expense"], 2),
        "total": round(totals["direct_cost"] + totals["operating_expense"], 2),
        "accounts": accounts,
    }


def purchase_monthly(start: date, end_exclusive: date) -> list[dict[str, Any]]:
    rows = search_read_all(
        "purchase.order",
        [["state", "in", ["purchase", "done"]], ["date_order", ">=", start.isoformat()], ["date_order", "<", end_exclusive.isoformat()]],
        ["amount_total", "date_order"],
        order="date_order asc",
    )
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        row_day = ksa_date(row.get("date_order"))
        if row_day:
            totals[row_day.strftime("%Y-%m")] += number(row.get("amount_total"))
    return [{"month": key, "amount": round(value, 2)} for key, value in sorted(totals.items())]


def stock_summary() -> dict[str, list[dict[str, Any]]]:
    rows = search_read_all(
        "stock.quant",
        [["location_id.usage", "=", "internal"], ["quantity", ">", 0]],
        ["product_id", "quantity", "reserved_quantity", "location_id"],
    )
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        _, location = many2one(row.get("location_id"), "غير محدد")
        _, product = many2one(row.get("product_id"), "غير محدد")
        result[location].append(
            {
                "product": product,
                "qty": round(number(row.get("quantity")), 1),
                "reserved": round(number(row.get("reserved_quantity")), 1),
            }
        )
    return dict(result)


def monthly_by_dimension(
    group_field: str,
    start: date,
    end_exclusive: date,
    *,
    include_aggregators_only: bool = False,
) -> dict[str, dict[str, float]]:
    domain = pos_domain(day_domain("date_order", start, end_exclusive))
    if include_aggregators_only:
        domain.append(["partner_id", "!=", False])
    groups = read_group_all("pos.order", domain, ["amount_total"], [group_field, "date_order:month"])
    result: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for group in groups:
        key = month_key(group.get("date_order:month"))
        raw_id, raw_name = many2one(group.get(group_field), "غير محدد")
        if not key:
            continue
        if group_field == "config_id":
            label = short_branch_name(raw_name)
        else:
            label = aggregator_name(raw_name)
            if not label:
                continue
        result[label][key] += number(group.get("amount_total"))
    return {
        label: {key: round(value, 2) for key, value in sorted(months.items(), reverse=True)}
        for label, months in result.items()
    }


# ─── Forecasting ───────────────────────────────────────────────────────────────
def forecast_next_day(daily_sales: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    target = today + timedelta(days=1)
    completed = [item for item in daily_sales if item.get("day") and date.fromisoformat(item["day"]) < today]
    equivalent_days = [
        item for item in completed
        if date.fromisoformat(item["day"]).weekday() == target.weekday()
    ][-8:]
    sample = equivalent_days if len(equivalent_days) >= 3 else completed[-7:]
    if len(sample) < 3:
        return None
    return {
        "target_date": target.isoformat(),
        "revenue": round(sum(number(item.get("revenue")) for item in sample) / len(sample), 2),
        "orders": round(sum(number(item.get("orders")) for item in sample) / len(sample)),
        "method": "same_weekday_recent_history" if sample is equivalent_days else "recent_7_complete_days",
        "sample_size": len(sample),
    }


def forecast_month(daily_sales: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    month_start = today.replace(day=1)
    completed = [
        item for item in daily_sales
        if item.get("day") and month_start <= date.fromisoformat(item["day"]) < today
    ]
    completed_days = (today - month_start).days
    if completed_days < 3:
        return None
    actual = sum(number(item.get("revenue")) for item in completed)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    daily_run_rate = actual / completed_days
    return {
        "projected_month_revenue": round(daily_run_rate * days_in_month, 2),
        "daily_run_rate": round(daily_run_rate, 2),
        "days_elapsed": completed_days,
        "days_in_month": days_in_month,
        "as_of": (today - timedelta(days=1)).isoformat(),
        "method": "closed_days_run_rate",
    }


def forecast_year(closed_ytd: dict[str, float | int], today: date) -> dict[str, Any] | None:
    year_start = date(today.year, 1, 1)
    completed_days = (today - year_start).days
    if completed_days < 14:
        return None
    days_in_year = 366 if calendar.isleap(today.year) else 365
    run_rate = number(closed_ytd.get("revenue")) / completed_days
    return {
        "projected_year_revenue": round(run_rate * days_in_year, 2),
        "daily_run_rate": round(run_rate, 2),
        "ytd_revenue": round(number(closed_ytd.get("revenue")), 2),
        "days_elapsed": completed_days,
        "days_in_year": days_in_year,
        "as_of": (today - timedelta(days=1)).isoformat(),
        "method": "closed_days_run_rate",
    }


# ─── Build and persist the dashboard snapshot ─────────────────────────────────
def build_snapshot(now: datetime) -> dict[str, Any]:
    today = now.date()
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)
    current_month_start = today.replace(day=1)
    next_month_start = add_months(current_month_start, 1)
    last_month_start = add_months(current_month_start, -1)
    last_year_start = date(today.year - 1, 1, 1)
    current_year_start = date(today.year, 1, 1)
    same_month_last_year_start = date(today.year - 1, today.month, 1)
    same_month_last_year_end = add_months(same_month_last_year_start, 1)
    current_week_start = week_start_sunday(today)
    days_completed_this_week = (today - current_week_start).days
    previous_week_aligned_start = current_week_start - timedelta(days=7)
    previous_week_aligned_end = previous_week_aligned_start + timedelta(days=days_completed_this_week)
    last_completed_week_start = current_week_start - timedelta(days=7)
    week_before_last_start = current_week_start - timedelta(days=14)
    year_before_last_start = date(today.year - 2, 1, 1)

    days_into_month = min(today.day, calendar.monthrange(last_month_start.year, last_month_start.month)[1])
    last_month_mtd_end = last_month_start + timedelta(days=days_into_month)
    same_month_ly_days = min(today.day, calendar.monthrange(same_month_last_year_start.year, same_month_last_year_start.month)[1])
    same_month_ly_mtd_end = same_month_last_year_start + timedelta(days=same_month_ly_days)

    log("Building KPI aggregates…")
    today_kpi = pos_total(day_domain("date_order", today, tomorrow))
    yesterday_kpi = pos_total(day_domain("date_order", yesterday, today))
    day_before_yesterday_kpi = pos_total(day_domain("date_order", yesterday - timedelta(days=1), yesterday))
    rolling_7_days = pos_total(day_domain("date_order", today - timedelta(days=6), tomorrow))
    prior_7_days = pos_total(day_domain("date_order", today - timedelta(days=13), today - timedelta(days=6)))
    current_wtd = pos_total(day_domain("date_order", current_week_start, today))
    prior_wtd = pos_total(day_domain("date_order", previous_week_aligned_start, previous_week_aligned_end))
    last_completed_week = pos_total(day_domain("date_order", last_completed_week_start, current_week_start))
    week_before_last = pos_total(day_domain("date_order", week_before_last_start, last_completed_week_start))
    month_to_date = pos_total(day_domain("date_order", current_month_start, tomorrow))
    last_month_full = pos_total(day_domain("date_order", last_month_start, current_month_start))
    last_month_mtd = pos_total(day_domain("date_order", last_month_start, last_month_mtd_end))
    same_month_last_year_full = pos_total(day_domain("date_order", same_month_last_year_start, same_month_last_year_end))
    same_month_last_year_mtd = pos_total(day_domain("date_order", same_month_last_year_start, same_month_ly_mtd_end))
    this_year_to_date = pos_total(day_domain("date_order", current_year_start, tomorrow))
    this_year_closed = pos_total(day_domain("date_order", current_year_start, today))
    equivalent_prior_year_day = date(
        today.year - 1,
        today.month,
        min(today.day, calendar.monthrange(today.year - 1, today.month)[1]),
    )
    lytd = pos_total(day_domain("date_order", last_year_start, equivalent_prior_year_day))
    last_year_full = pos_total(day_domain("date_order", last_year_start, current_year_start))
    year_before_last_full = pos_total(day_domain("date_order", year_before_last_start, last_year_start))
    all_time = pos_total()

    log("Building complete daily and monthly sales series…")
    daily_start = today - timedelta(days=59)
    daily_raw = pos_daily_range(daily_start, tomorrow)
    monthly_raw = pos_monthly_range(HISTORY_START, tomorrow)
    twelve_month_start = add_months(current_month_start, -11)

    log("Building breakdowns and operational data…")
    branch_periods = {
        "today": pos_by_branch(day_domain("date_order", today, tomorrow)),
        "yesterday": pos_by_branch(day_domain("date_order", yesterday, today)),
        "this_month": pos_by_branch(day_domain("date_order", current_month_start, tomorrow)),
        "last_month": pos_by_branch(day_domain("date_order", last_month_start, current_month_start)),
        "this_year": pos_by_branch(day_domain("date_order", current_year_start, tomorrow)),
        "all_time": pos_by_branch(),
    }
    aggregator_periods = {
        "all_time": pos_by_aggregator(),
        "last_30d": pos_by_aggregator(day_domain("date_order", today - timedelta(days=29), tomorrow)),
        "this_month": pos_by_aggregator(day_domain("date_order", current_month_start, tomorrow)),
        "this_year": pos_by_aggregator(day_domain("date_order", current_year_start, tomorrow)),
    }

    payment_periods = {
        "last_30d": payment_breakdown(day_domain("payment_date", today - timedelta(days=29), tomorrow)),
        "this_month": payment_breakdown(day_domain("payment_date", current_month_start, tomorrow)),
        "this_year": payment_breakdown(day_domain("payment_date", current_year_start, tomorrow)),
    }

    branch_monthly = monthly_by_dimension("config_id", HISTORY_START, tomorrow)
    aggregator_monthly = monthly_by_dimension("partner_id", HISTORY_START, tomorrow, include_aggregators_only=True)
    pl_raw = pl_monthly(HISTORY_START)
    expense_structure = expense_breakdown(twelve_month_start, tomorrow)
    purchase_raw = purchase_monthly(HISTORY_START, tomorrow)
    product_periods = {
        "last_30d": top_products(day_domain("order_id.date_order", today - timedelta(days=29), tomorrow)),
        "this_month": top_products(day_domain("order_id.date_order", current_month_start, tomorrow)),
        "this_year": top_products(day_domain("order_id.date_order", current_year_start, tomorrow)),
        "all_time": top_products(),
    }

    log("Building decision-center metrics…")
    complete_daily = [item for item in daily_raw if date.fromisoformat(str(item.get("day"))) < today]
    decision_hourly = pos_hourly_range(today - timedelta(days=56), today)
    heatmap = pos_weekday_hour_range(today - timedelta(days=56), today)
    hourly_revenue = sum(number(item.get("revenue")) for item in decision_hourly)
    peak_revenue = sum(number(item.get("revenue")) for item in decision_hourly if 17 <= int(item.get("hour", -1)) <= 20)
    decision_branches = pos_by_branch(day_domain("date_order", current_month_start, today))
    decision_platforms = pos_by_aggregator(day_domain("date_order", current_month_start, today))
    decision_total = pos_total(day_domain("date_order", current_month_start, today))
    decision_payments = payment_breakdown(day_domain("payment_date", current_month_start, today))
    decision_products = top_products(day_domain("order_id.date_order", today - timedelta(days=30), today), limit=100)
    decision_center = {
        "as_of": yesterday.isoformat(),
        "rolling_sales": rolling_sales_metrics(complete_daily),
        "demand_timing": {
            "hourly": decision_hourly,
            "weekday_hour": heatmap,
            "peak_window": "17:00–20:59",
            "peak_revenue": round(peak_revenue, 2),
            "peak_share_pct": round(peak_revenue / hourly_revenue * 100, 1) if hourly_revenue else 0.0,
        },
        "channels": channel_portfolio(decision_platforms, decision_total),
        "branches": branch_portfolio(decision_branches),
        "payments": payment_portfolio(decision_payments),
        "products": product_portfolio(decision_products),
        "expenses": expense_pareto(expense_structure),
        "profitability": profitability_quality(pl_raw),
        "limitations": {
            "channel_margin": "يعرض الإيراد والمزيج فقط؛ لا تتوفر رسوم المنصات والخصومات والمرتجعات بعد.",
            "branch_profit": "يعرض أداء المبيعات فقط؛ لا تتوفر تكاليف مخصصة لكل فرع بعد.",
            "product_margin": "هامش المنتج تقديري مبني على التكلفة المعيارية الحالية.",
        },
    }
    purchases_recent = search_read_all(
        "purchase.order",
        [["state", "in", ["purchase", "done"]]],
        ["name", "partner_id", "amount_total", "date_order"],
        order="date_order desc",
        page_size=20,
    )[:20]

    total_aggregator_revenue = sum(number(item.get("revenue")) for item in aggregator_periods["all_time"].values())
    daily_days_with_orders = sum(1 for item in daily_raw if number(item.get("orders")) > 0)

    return {
        "meta": {
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S KSA"),
            "generated_at_iso": now.isoformat(),
            "today": today.isoformat(),
            "yesterday": yesterday.isoformat(),
            "this_month": today.strftime("%B %Y"),
            "currency": "SAR",
            "timezone": "Asia/Riyadh",
            "last_completed_day": yesterday.isoformat(),
            "schema_version": 8,
        },
        "data_health": {
            "status": "ok",
            "daily_sales": {
                "expected_days": len(daily_raw),
                "days_with_orders": daily_days_with_orders,
                "range_start": daily_start.isoformat(),
                "range_end": today.isoformat(),
            },
            "monthly_sales_months": len(monthly_raw),
            "expense_breakdown_accounts": len(expense_structure["accounts"]),
            "decision_center": {
                "status": "ok",
                "rolling_complete_days": len(complete_daily),
                "heatmap_cells": len(heatmap),
                "product_cost_coverage_pct": decision_center["products"]["cost_coverage_pct"],
            },
        },
        "kpis": {
            "today": today_kpi,
            "yesterday": yesterday_kpi,
            "dtd": yesterday_kpi,
            "wtd": current_wtd,
            "ytd": this_year_closed,
            "last_7_days": rolling_7_days,
            "this_month": month_to_date,
            "last_month": last_month_full,
            "this_year": this_year_to_date,
            "last_year": last_year_full,
            "all_time": all_time,
        },
        "overview_comparisons": {
            "today": {"baseline": yesterday_kpi, "label": "مقارنة بأمس"},
            "yesterday": {"baseline": day_before_yesterday_kpi, "label": "مقارنة باليوم الذي قبله"},
            "dtd": {"baseline": day_before_yesterday_kpi, "label": "مقارنة باليوم الذي قبله"},
            "wtd": {"baseline": prior_wtd, "label": "مقارنة بنفس أيام الأسبوع السابق"},
            "ytd": {"baseline": lytd, "label": "مقارنة بالفترة المناظرة من العام الماضي"},
            "last_7_days": {"baseline": prior_7_days, "label": "مقارنة بالـ 7 أيام السابقة"},
            "this_month": {"baseline": last_month_mtd, "label": "مقارنة بنفس عدد الأيام من الشهر الماضي"},
            "last_month": {"baseline": same_month_last_year_full, "label": "مقارنة بنفس الشهر العام الماضي"},
            "this_year": {"baseline": lytd, "label": "مقارنة بنفس الفترة المكتملة العام الماضي"},
            "last_year": {"baseline": year_before_last_full, "label": "مقارنة بالعام الذي قبله"},
            "all_time": {"baseline": None, "label": "إجمالي تراكمي"},
        },
        "period_metrics": {
            "dtd": {
                "current": yesterday_kpi,
                "baseline": day_before_yesterday_kpi,
                "as_of": yesterday.isoformat(),
                "current_label": "آخر يوم مكتمل",
                "baseline_label": "اليوم الذي قبله",
            },
            "wtd": {
                "current": current_wtd,
                "baseline": prior_wtd,
                "start": current_week_start.isoformat(),
                "as_of": yesterday.isoformat(),
                "baseline_start": previous_week_aligned_start.isoformat(),
                "baseline_end": (previous_week_aligned_end - timedelta(days=1)).isoformat(),
                "current_label": "الأسبوع حتى آخر يوم مكتمل",
                "baseline_label": "نفس أيام الأسبوع السابق",
            },
            "ytd": {
                "current": this_year_closed,
                "baseline": lytd,
                "start": current_year_start.isoformat(),
                "as_of": yesterday.isoformat(),
                "baseline_start": last_year_start.isoformat(),
                "baseline_end": (equivalent_prior_year_day - timedelta(days=1)).isoformat(),
                "current_label": "السنة حتى آخر يوم مكتمل",
                "baseline_label": "الفترة المناظرة من العام الماضي",
            },
        },
        "daily_sales": list(reversed(daily_raw)),
        "daily_comparison": {
            "today": today_kpi,
            "yesterday": yesterday_kpi,
            "latest_completed_day": yesterday_kpi,
            "prior_completed_day": day_before_yesterday_kpi,
            "same_day_last_week": pos_total(day_domain("date_order", yesterday - timedelta(days=7), yesterday - timedelta(days=6))),
            "forecast_tomorrow": forecast_next_day(daily_raw, today),
        },
        "weekly_comparison": {
            "last_completed_week": last_completed_week,
            "week_before_last": week_before_last,
            "week_start": last_completed_week_start.isoformat(),
            "week_end": (current_week_start - timedelta(days=1)).isoformat(),
        },
        "monthly_sales_all": list(reversed(monthly_raw)),
        "monthly_sales_12m": list(reversed([item for item in monthly_raw if item["month"] >= twelve_month_start.strftime("%Y-%m")])),
        "monthly_comparison": {
            "this_month": month_to_date,
            "last_month_full": last_month_full,
            "last_month_mtd": last_month_mtd,
            "same_month_ly": same_month_last_year_mtd,
            "forecast_month": forecast_month(daily_raw, today),
        },
        "yearly_comparison": {
            "this_year": this_year_closed,
            "last_year": last_year_full,
            "last_completed_year": last_year_full,
            "year_before_last": year_before_last_full,
            "lytd": lytd,
            "forecast_year": forecast_year(this_year_closed, today),
        },
        "hourly_sales": pos_hourly_range(today - timedelta(days=13), tomorrow),
        "branches": branch_periods,
        "branch_monthly": branch_monthly,
        "aggregators": {**aggregator_periods, "monthly": aggregator_monthly},
        "payment_methods": payment_periods,
        "top_products": product_periods,
        "decision_center": decision_center,
        "pl_monthly": list(reversed(pl_raw)),
        "expense_breakdown": expense_structure,
        "purchases": {
            "monthly": list(reversed(purchase_raw)),
            "total": round(sum(number(item.get("amount")) for item in purchase_raw), 2),
            "recent": purchases_recent,
        },
        "stock": stock_summary(),
        "summary": {
            "total_revenue": round(number(all_time.get("revenue")), 2),
            "total_orders": int(all_time.get("orders", 0)),
            "aggregator_share": round(total_aggregator_revenue / number(all_time.get("revenue")) * 100, 1) if number(all_time.get("revenue")) > 0 else 0,
            "aggregator_total": round(total_aggregator_revenue, 2),
        },
    }


def save_snapshot(data: dict[str, Any]) -> None:
    with open(OUT_FILE, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(OUT_FILE) / 1024
    log(f"Snapshot saved to {OUT_FILE} ({size_kb:.1f} KB).")


def main() -> None:
    configure()
    connect()
    now = datetime.now(KSA)
    data = build_snapshot(now)
    save_snapshot(data)
    log(
        "Completed: "
        f"today={number(data['kpis']['today']['revenue']):,.0f} SAR, "
        f"orders={data['kpis']['today']['orders']}."
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("Interrupted")
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
