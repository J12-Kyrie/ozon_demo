"""Rule-based estimators for missing business fields."""

from __future__ import annotations

import pandas as pd


def estimate_margin(row: pd.Series, cfg: dict) -> float | None:
    price = row.get("绿标价")
    if price is None or pd.isna(price) or float(price) <= 0:
        return None
    purchase = row.get("阿里巴巴采购价(预)")
    freight = row.get("自定义1688运费金额")
    purchase = 0.0 if purchase is None or pd.isna(purchase) else float(purchase)
    freight = 0.0 if freight is None or pd.isna(freight) else float(freight)
    commission = float(cfg.get("commission_rate_default", 0.14))
    ad_rate = float(cfg.get("ad_rate_default", 0.05))
    return_loss = float(cfg.get("return_loss_rate", 0.03))
    total_cost = (
        purchase + freight + float(price) * (commission + ad_rate + return_loss)
    )
    margin = (float(price) - total_cost) / float(price) * 100.0
    return max(-100.0, min(100.0, margin))


def estimate_conversion(row: pd.Series, cfg: dict) -> float | None:
    weights = cfg.get("conversion_weights", {})
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
        0.0
        if rating is None or pd.isna(rating)
        else min(max(float(rating) / 5.0, 0.0), 1.0)
    )
    reviews_s = (
        0.0 if reviews is None or pd.isna(reviews) else min(float(reviews) / 500.0, 1.0)
    )
    price_s = (
        0.5
        if price is None or pd.isna(price)
        else (0.7 if float(price) <= 200 else 0.4)
    )
    comp_s = (
        0.5
        if comp is None or pd.isna(comp)
        else max(0.0, min(1.0, 1.0 - float(comp) / 30.0))
    )

    score = (
        rating_s * w_rating
        + reviews_s * w_reviews
        + price_s * w_price
        + comp_s * w_comp
    ) / total_w
    return max(0.0, min(12.0, score * 12.0))


def estimate_confidence(row: pd.Series, cfg: dict) -> float:
    rules = cfg.get("confidence_rules", {})
    required_fields = rules.get("required_fields", ["绿标价", "评级", "跟卖数量"])
    filled = 0
    for field in required_fields:
        value = row.get(field)
        if value is not None and not pd.isna(value):
            filled += 1
    ratio = filled / max(len(required_fields), 1)
    return round(ratio, 2)


def apply_estimates(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    est_cfg = cfg.get("estimator", {})
    if not est_cfg.get("enabled", False) or df.empty:
        return df

    out = df.copy()
    out["毛利率_est"] = out.apply(lambda r: estimate_margin(r, est_cfg), axis=1)
    out["展示至下单转化率_est"] = out.apply(
        lambda r: estimate_conversion(r, est_cfg), axis=1
    )
    out["estimate_confidence"] = out.apply(
        lambda r: estimate_confidence(r, est_cfg), axis=1
    )
    out["毛利率_est"] = out["毛利率_est"].round(2)
    out["展示至下单转化率_est"] = out["展示至下单转化率_est"].round(2)
    out["estimate_version"] = str(est_cfg.get("version", "v1"))
    return out
