"""Rule-based estimators for missing business fields.

All modeled values are emitted as ``*_est`` columns. Scraped public fields and
seller/private fields remain untouched so the UI can distinguish facts from
model output.
"""

from __future__ import annotations

import math
import re

import pandas as pd


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
}


def _num(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.replace("%", "").replace(",", ".").strip()
        if not value:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(value, default: float) -> float:
    parsed = _num(value)
    if parsed is None:
        return default
    return parsed / 100.0 if parsed > 1 else parsed


def _category_value(row: pd.Series, cfg: dict, key: str, default):
    table = cfg.get(key, {}) or {}
    if not isinstance(table, dict):
        return default
    categories = [
        str(row.get("三级类目") or "").strip(),
        str(row.get("一级类目") or "").strip(),
    ]
    for category in categories:
        if not category:
            continue
        if category in table:
            return table[category]
        category_l = category.lower()
        for configured, value in table.items():
            configured_l = str(configured).lower()
            if configured_l and (configured_l in category_l or category_l in configured_l):
                return value
    return default


def _dimension_weight_kg(row: pd.Series, cfg: dict) -> float | None:
    dims = row.get("长*宽*高(mm)")
    if dims is None or pd.isna(dims):
        return None
    parts = re.findall(r"\d+(?:[.,]\d+)?", str(dims))
    if len(parts) < 3:
        return None
    length_mm, width_mm, height_mm = [float(p.replace(",", ".")) for p in parts[:3]]
    divisor = float(cfg.get("volumetric_divisor", 5000))
    return (length_mm / 10) * (width_mm / 10) * (height_mm / 10) / divisor


def _chargeable_weight_kg(row: pd.Series, cfg: dict) -> float:
    grams = _num(row.get("重量（克）"))
    actual = grams / 1000.0 if grams and grams > 0 else 0.0
    volumetric = _dimension_weight_kg(row, cfg) or 0.0
    default = float(cfg.get("default_chargeable_weight_kg", 0.3))
    return max(actual, volumetric, default)


def estimate_commission_rate(row: pd.Series, cfg: dict) -> float:
    default = _rate(cfg.get("commission_rate_default", 0.14), 0.14)
    configured = _category_value(row, cfg, "category_commission_rates", default)
    return _rate(configured, default)


def estimate_purchase_price(row: pd.Series, cfg: dict) -> float | None:
    purchase = _num(row.get("阿里巴巴采购价(预)"))
    if purchase is not None:
        return purchase
    price = _num(row.get("绿标价"))
    if price is None or price <= 0:
        return None
    fx = float(cfg.get("cny_to_rub_rate", 12.0))
    ratio_default = float(cfg.get("purchase_ratio_default", 0.35))
    ratio = float(_category_value(row, cfg, "category_purchase_ratio", ratio_default))
    return price * ratio / max(fx, 0.01)


def estimate_1688_freight(row: pd.Series, cfg: dict) -> float:
    freight = _num(row.get("自定义1688运费金额"))
    if freight is not None:
        return freight
    return float(cfg.get("supplier_freight_default", 8.0))


def estimate_ozon_logistics(row: pd.Series, cfg: dict) -> float | None:
    existing = _num(row.get("ozon物流费(预)"))
    if existing is not None:
        return existing
    price = _num(row.get("绿标价"))
    if price is None or price <= 0:
        return None
    base = float(cfg.get("ozon_logistics_base_default", 180.0))
    per_kg = float(cfg.get("ozon_logistics_per_kg_default", 60.0))
    weight_kg = math.ceil(_chargeable_weight_kg(row, cfg) * 2) / 2
    return base + per_kg * weight_kg


def estimate_platform_commission(row: pd.Series, cfg: dict) -> float | None:
    price = _num(row.get("绿标价"))
    if price is None or price <= 0:
        return None
    return price * estimate_commission_rate(row, cfg)


def estimate_total_cost(row: pd.Series, cfg: dict) -> float | None:
    price = _num(row.get("绿标价"))
    if price is None or price <= 0:
        return None
    purchase = estimate_purchase_price(row, cfg)
    logistics = estimate_ozon_logistics(row, cfg)
    commission = estimate_platform_commission(row, cfg)
    if purchase is None or logistics is None or commission is None:
        return None
    fx = float(cfg.get("cny_to_rub_rate", 12.0))
    ad_rate = _rate(cfg.get("ad_rate_default", 0.05), 0.05)
    return_loss = _rate(cfg.get("return_loss_rate", 0.03), 0.03)
    return (
        (purchase + estimate_1688_freight(row, cfg)) * fx
        + logistics
        + commission
        + price * (ad_rate + return_loss)
    )


def estimate_profit(row: pd.Series, cfg: dict) -> float | None:
    price = _num(row.get("绿标价"))
    total_cost = estimate_total_cost(row, cfg)
    if price is None or total_cost is None:
        return None
    return price - total_cost


def estimate_margin(row: pd.Series, cfg: dict) -> float | None:
    price = _num(row.get("绿标价"))
    total_cost = estimate_total_cost(row, cfg)
    if price is None or price <= 0 or total_cost is None:
        return None
    margin = (price - total_cost) / price * 100.0
    return max(-100.0, min(100.0, margin))


def estimate_cost_share(row: pd.Series, cfg: dict) -> float | None:
    price = _num(row.get("绿标价"))
    total_cost = estimate_total_cost(row, cfg)
    if price is None or price <= 0 or total_cost is None:
        return None
    return total_cost / price * 100.0


def estimate_monthly_sales(row: pd.Series, cfg: dict) -> float | None:
    reviews = _num(row.get("评论数"))
    if reviews is None:
        return None
    rate_default = float(cfg.get("review_to_monthly_sales_rate_default", 0.35))
    rate = float(_category_value(row, cfg, "category_review_to_sales_rate", rate_default))
    rating = _num(row.get("评级"))
    competitors = _num(row.get("跟卖数量"))
    rating_factor = 1.0 if rating is None else min(max(0.7 + (rating - 4.0) * 0.2, 0.6), 1.2)
    competition_factor = 1.0 if competitors is None else 1.0 / (1.0 + competitors * 0.03)
    sales = reviews * rate * rating_factor * competition_factor
    return max(0.0, min(float(cfg.get("monthly_sales_est_cap", 5000)), sales))


def estimate_monthly_revenue(row: pd.Series, cfg: dict) -> float | None:
    price = _num(row.get("绿标价"))
    sales = estimate_monthly_sales(row, cfg)
    if price is None or sales is None:
        return None
    return price * sales


def estimate_conversion(row: pd.Series, cfg: dict) -> float | None:
    weights = cfg.get("conversion_weights", {})
    w_rating = float(weights.get("rating", 0.5))
    w_reviews = float(weights.get("reviews", 0.2))
    w_price = float(weights.get("price_competitiveness", 0.2))
    w_comp = float(weights.get("competition", 0.1))
    total_w = w_rating + w_reviews + w_price + w_comp
    if total_w <= 0:
        total_w = 1.0

    rating = _num(row.get("评级"))
    reviews = _num(row.get("评论数"))
    price = _num(row.get("绿标价"))
    comp = _num(row.get("跟卖数量"))

    rating_s = 0.0 if rating is None else min(max(rating / 5.0, 0.0), 1.0)
    reviews_s = 0.0 if reviews is None else min(reviews / 500.0, 1.0)
    price_s = 0.5 if price is None else (0.7 if price <= 200 else 0.4)
    comp_s = 0.5 if comp is None else max(0.0, min(1.0, 1.0 - comp / 30.0))

    score = (
        rating_s * w_rating
        + reviews_s * w_reviews
        + price_s * w_price
        + comp_s * w_comp
    ) / total_w
    return max(0.0, min(12.0, score * 12.0))


def estimate_confidence(row: pd.Series, cfg: dict) -> float:
    rules = cfg.get("confidence_rules", {})
    required_fields = rules.get("required_fields", ["绿标价", "评级", "评论数", "跟卖数量"])
    filled = 0
    for field in required_fields:
        value = row.get(field)
        if value is not None and not pd.isna(value):
            filled += 1
    return round(filled / max(len(required_fields), 1), 2)


def apply_estimates(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    est_cfg = cfg.get("estimator", {})
    if not est_cfg.get("enabled", False) or df.empty:
        return df

    out = df.copy()
    out["阿里巴巴采购价(预)_est"] = out.apply(lambda r: estimate_purchase_price(r, est_cfg), axis=1)
    out["自定义1688运费金额_est"] = out.apply(lambda r: estimate_1688_freight(r, est_cfg), axis=1)
    out["ozon物流费(预)_est"] = out.apply(lambda r: estimate_ozon_logistics(r, est_cfg), axis=1)
    out["佣金比例%_est"] = out.apply(lambda r: estimate_commission_rate(r, est_cfg) * 100.0, axis=1)
    out["平台佣金(预)_est"] = out.apply(lambda r: estimate_platform_commission(r, est_cfg), axis=1)
    out["总成本_est"] = out.apply(lambda r: estimate_total_cost(r, est_cfg), axis=1)
    out["利润(预)_est"] = out.apply(lambda r: estimate_profit(r, est_cfg), axis=1)
    out["成本占比_est"] = out.apply(lambda r: estimate_cost_share(r, est_cfg), axis=1)
    out["毛利率_est"] = out.apply(lambda r: estimate_margin(r, est_cfg), axis=1)
    out["月销量_est"] = out.apply(lambda r: estimate_monthly_sales(r, est_cfg), axis=1)
    out["月销售额_est"] = out.apply(lambda r: estimate_monthly_revenue(r, est_cfg), axis=1)
    out["展示至下单转化率_est"] = out.apply(lambda r: estimate_conversion(r, est_cfg), axis=1)
    out["estimate_confidence"] = out.apply(lambda r: estimate_confidence(r, est_cfg), axis=1)
    out["estimate_version"] = str(est_cfg.get("version", "v2"))

    for col in out.columns:
        if col.endswith("_est") and pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col].round(2)
    return out
