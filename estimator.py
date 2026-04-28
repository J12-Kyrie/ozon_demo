"""Rule-based estimators for missing business fields.

Sales, conversion, margin, cost, and profit are estimated from
publicly available Ozon fields + configurable rates.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import math


# --------------- configurable rate tables ---------------

# Ozon commission rates by category (Russian category name → rate)
CATEGORY_COMMISSION_RATES = {
    "Электроника": 0.10,
    "Бытовая техника": 0.12,
    "Одежда": 0.15,
    "Обувь": 0.15,
    "Красота": 0.13,
    "Товары для дома": 0.13,
    "Детские товары": 0.10,
    "Спорт": 0.12,
    "Зоотовары": 0.12,
    "Книги": 0.10,
    "автотовары": 0.10,
    "продукты": 0.10,
    "стройматериалы": 0.10,
    "аптека": 0.10,
    "ювелирные": 0.12,
    "мебель": 0.14,
    "канцтовары": 0.10,
}

# Review-to-sales conversion ratios by category
REVIEW_SALES_RATIO = {
    "Электроника": 20,
    "Бытовая техника": 15,
    "Одежда": 5,
    "Обувь": 4,
    "Красота": 10,
    "Товары для дома": 10,
    "Детские товары": 12,
    "Спорт": 8,
    "Зоотовары": 10,
}

LOGISTIC_BASE_RATES = {
    "fbs": 150,
    "fbo": 100,
    "small": 50,
    "default": 150,
}
LOGISTIC_WEIGHT_RATE = 25  # ₽ per 500g
CNY_TO_RUB = 12.0  # approximate exchange rate


def _get_category_rate(cat_name, table, default):
    """Match Russian category name against a rate table."""
    if not isinstance(cat_name, str) or not cat_name.strip():
        return default
    lower = cat_name.strip().lower()
    for key, rate in table.items():
        if key.lower() in lower or lower in key.lower():
            return rate
    return default


# --------------- estimation functions ---------------


def estimate_margin(row: pd.Series, cfg: dict) -> float | None:
    """Estimate gross margin: (price - total_cost) / price × 100."""
    price = row.get("绿标价")
    if price is None or pd.isna(price) or float(price) <= 0:
        return None

    est_cfg = cfg.get("estimator", {})
    price_f = float(price)

    # Purchase price: from 1688 if available, else from category ratio fallback
    purchase = row.get("阿里巴巴采购价(预)")
    if purchase is None or pd.isna(purchase):
        ratio = float(est_cfg.get("category_purchase_ratio", {}).get(
            str(row.get("一级类目", "")), 0.15
        ))
        purchase = price_f * ratio
    else:
        purchase = float(purchase)

    # Freight: from 1688 if available, else default
    freight = row.get("自定义1688运费金额")
    if freight is None or pd.isna(freight) or float(freight) <= 0:
        freight = price_f * 0.03  # 3% of price as rough freight est
    else:
        freight = float(freight)

    # Commission: category-aware
    cat = row.get("一级类目", "")
    commission_rate = _get_category_rate(
        cat, CATEGORY_COMMISSION_RATES,
        float(est_cfg.get("commission_rate_default", 0.14)),
    )
    commission = price_f * commission_rate

    # Ad + loss
    ad_rate = float(est_cfg.get("ad_rate_default", 0.05))
    return_loss = float(est_cfg.get("return_loss_rate", 0.03))
    extra = price_f * (ad_rate + return_loss)

    total_cost = purchase + freight + commission + extra
    margin = (price_f - total_cost) / price_f * 100.0
    return round(max(-100.0, min(100.0, margin)), 2)


def estimate_conversion(row: pd.Series, cfg: dict) -> float | None:
    """Estimate show-to-order conversion rate from rating/reviews/price/competition."""
    est_cfg = cfg.get("estimator", {})
    weights = est_cfg.get("conversion_weights", {})
    w_rating = float(weights.get("rating", 0.5))
    w_reviews = float(weights.get("reviews", 0.2))
    w_price = float(weights.get("price_competitiveness", 0.2))
    w_comp = float(weights.get("competition", 0.1))
    total_w = w_rating + w_reviews + w_price + w_comp
    if total_w <= 0:
        total_w = 1.0

    rating = row.get("评级")
    reviews = row.get("评论数", 0)
    price = row.get("绿标价")
    comp = row.get("跟卖数量")

    rating_s = (
        0.0 if rating is None or pd.isna(rating)
        else min(max(float(rating) / 5.0, 0.0), 1.0)
    )
    reviews_s = (
        0.0 if reviews is None or pd.isna(reviews)
        else min(float(reviews) / 500.0, 1.0)
    )
    price_s = (
        0.5 if price is None or pd.isna(price)
        else (0.7 if float(price) <= 200 else 0.4)
    )
    comp_s = (
        0.5 if comp is None or pd.isna(comp)
        else max(0.0, min(1.0, 1.0 - float(comp) / 30.0))
    )

    score = (
        rating_s * w_rating
        + reviews_s * w_reviews
        + price_s * w_price
        + comp_s * w_comp
    ) / total_w
    return round(max(0.0, min(12.0, score * 12.0)), 2)


def estimate_monthly_sales(row: pd.Series, cfg: dict) -> float | None:
    """Estimate monthly sales from review count × category ratio."""
    reviews = row.get("评论数")
    if reviews is None or pd.isna(reviews) or float(reviews) <= 0:
        return None
    cat = str(row.get("一级类目", ""))
    ratio = _get_category_ratio(cat, REVIEW_SALES_RATIO, 15)
    return round(float(reviews) * ratio, 0)


def estimate_monthly_revenue(row: pd.Series, cfg: dict) -> float | None:
    """Estimate monthly revenue = price × monthly_sales_est."""
    price = row.get("绿标价")
    sales_est = row.get("月销量_est")
    if (
        price is None or pd.isna(price)
        or sales_est is None or pd.isna(sales_est)
    ):
        return None
    return round(float(price) * float(sales_est), 2)


def estimate_logistic_fee(row: pd.Series, cfg: dict) -> float | None:
    """Estimate Ozon logistic fee from delivery method and weight."""
    weight = row.get("重量（克）")
    method = str(row.get("配送方式", ""))
    base = LOGISTIC_BASE_RATES.get(
        method.lower() if method and not pd.isna(method) else "default",
        LOGISTIC_BASE_RATES["default"],
    )
    if weight is None or pd.isna(weight) or float(weight) <= 0:
        return float(base)
    # 25₽ per 500g beyond first 500g
    extra = math.ceil(max(0, float(weight) - 500) / 500) * LOGISTIC_WEIGHT_RATE
    return round(float(base) + extra, 2)


def estimate_commission(row: pd.Series, cfg: dict) -> float | None:
    """Estimate Ozon platform commission from price × category rate."""
    price = row.get("绿标价")
    if price is None or pd.isna(price) or float(price) <= 0:
        return None
    est_cfg = cfg.get("estimator", {})
    cat = str(row.get("一级类目", ""))
    rate = _get_category_rate(
        cat, CATEGORY_COMMISSION_RATES,
        float(est_cfg.get("commission_rate_default", 0.14)),
    )
    return round(float(price) * rate, 2)


def estimate_confidence(row: pd.Series, cfg: dict) -> float:
    """Confidence score based on required field completeness."""
    est_cfg = cfg.get("estimator", {})
    rules = est_cfg.get("confidence_rules", {})
    required_fields = rules.get("required_fields", ["绿标价", "评级", "跟卖数量"])
    filled = sum(
        1 for f in required_fields
        if row.get(f) is not None and not pd.isna(row.get(f))
    )
    return round(filled / max(len(required_fields), 1), 2)


def _get_category_ratio(cat_name, table, default):
    """Match Russian category name against a ratio table (alias for _get_category_rate)."""
    return _get_category_rate(cat_name, table, default)


# --------------- pipeline entry point ---------------


def apply_estimates(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Apply all rule-based estimators to a DataFrame.

    Adds _est columns for: margin, conversion, monthly_sales, monthly_revenue,
    logistic_fee, commission, profit, total_cost, cost_ratio.
    Does NOT overwrite real columns (only writes to _est suffixed columns).
    """
    est_cfg = cfg.get("estimator", {})
    if not est_cfg.get("enabled", False) or df.empty:
        return df

    out = df.copy()

    # Core estimates
    out["毛利率_est"] = out.apply(lambda r: estimate_margin(r, cfg), axis=1)
    out["展示至下单转化率_est"] = out.apply(lambda r: estimate_conversion(r, cfg), axis=1)
    out["月销量_est"] = out.apply(lambda r: estimate_monthly_sales(r, cfg), axis=1)
    out["月销售额_est"] = out.apply(lambda r: estimate_monthly_revenue(r, cfg), axis=1)

    # Cost-related estimates
    out["平台佣金_est"] = out.apply(lambda r: estimate_commission(r, cfg), axis=1)
    out["ozon物流费_est"] = out.apply(lambda r: estimate_logistic_fee(r, cfg), axis=1)

    # Derived cost/profit estimates (order matters: margin sets commission dependency)
    def _estimate_total_cost(row):
        price = row.get("绿标价")
        if price is None or pd.isna(price) or float(price) <= 0:
            return None
        margin_est = row.get("毛利率_est")
        if margin_est is None or pd.isna(margin_est):
            return None
        return round(float(price) * (1 - float(margin_est) / 100), 2)

    def _estimate_profit(row):
        price = row.get("绿标价")
        cost = row.get("总成本_est")
        if price is None or cost is None or pd.isna(price) or pd.isna(cost):
            return None
        return round(float(price) - float(cost), 2)

    def _estimate_cost_ratio(row):
        cost = row.get("总成本_est")
        price = row.get("绿标价")
        if cost is None or price is None or pd.isna(cost) or pd.isna(price) or float(price) <= 0:
            return None
        return round(float(cost) / float(price) * 100, 1)

    out["总成本_est"] = out.apply(_estimate_total_cost, axis=1)
    out["利润_est"] = out.apply(_estimate_profit, axis=1)
    out["成本占比_est"] = out.apply(_estimate_cost_ratio, axis=1)

    # Confidence
    out["estimate_confidence"] = out.apply(lambda r: estimate_confidence(r, cfg), axis=1)
    out["estimate_version"] = str(est_cfg.get("version", "v2"))

    # Round all estimate columns
    for col in out.columns:
        if col.endswith("_est") and out[col].dtype in ("float64", "float32"):
            out[col] = out[col].round(2)

    return out
