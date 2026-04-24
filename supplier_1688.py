"""1688 supplier enrichment (keyword-first, manual mapping driven).

This module intentionally keeps a conservative implementation:
- Mapping is required; if keyword is empty -> mapping_miss.
- Keyword-first strategy; URL is only fallback metadata.
- Category match is based on third-level category keywords.
- Slider/captcha handling is manual intervention (status only).
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
import pandas as pd


@dataclass
class SupplierRow:
    sku_id: int
    purchase_price: float | None
    freight_est: float | None
    status: str


def load_mapping(mapping_file: str) -> dict[int, dict]:
    if not os.path.exists(mapping_file):
        return {}
    mapping = {}
    with open(mapping_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = str(row.get("ozon_sku", "")).strip()
            if not sku.isdigit():
                continue
            raw_price = (row.get("purchase_price") or "").strip()
            raw_freight = (row.get("freight_est") or "").strip()
            mapping[int(sku)] = {
                "supplier_keyword": (row.get("supplier_keyword") or "").strip(),
                "supplier_url": (row.get("supplier_url") or "").strip(),
                "note": (row.get("note") or "").strip(),
                "purchase_price": float(raw_price) if raw_price else None,
                "freight_est": float(raw_freight) if raw_freight else None,
            }
    return mapping


def _keyword_match(category3: str, supplier_keyword: str) -> bool:
    if not category3 or not supplier_keyword:
        return False
    cat_tokens = [t for t in category3.replace("/", " ").split() if t]
    if not cat_tokens:
        return False
    lower_kw = supplier_keyword.lower()
    return any(token.lower() in lower_kw for token in cat_tokens)


def resolve_supplier_target(item: dict, cfg: dict) -> dict | None:
    strategy = cfg.get("mapping_priority", "keyword_first")
    keyword = (item.get("supplier_keyword") or "").strip()
    url = (item.get("supplier_url") or "").strip()
    if strategy == "keyword_first":
        if keyword:
            return {"type": "keyword", "value": keyword}
        if url:
            return {"type": "url", "value": url}
    else:
        if url:
            return {"type": "url", "value": url}
        if keyword:
            return {"type": "keyword", "value": keyword}
    return None


def open_page_1688(page, target: dict) -> dict:
    """Navigation placeholder for future real browser automation."""
    return {"status": "ready", "target": target}


def is_category_match(ozon_category: str, supplier_item: dict, cfg: dict) -> bool:
    if cfg.get("match_rule", "category_based") != "category_based":
        return True
    keyword = str(supplier_item.get("supplier_keyword") or "")
    return _keyword_match(str(ozon_category or ""), keyword)


def fetch_supplier_fields(mapped_item: dict, cfg: dict) -> dict:
    """Fetch supplier fields from mapping/defaults (no live crawl yet)."""
    defaults = cfg.get("default_values", {})
    global_freight = defaults.get("global_freight", 8.0)
    freight = mapped_item.get("freight_est")
    if freight is None:
        freight = global_freight
    return {
        "purchase_price": mapped_item.get("purchase_price"),
        "freight_est": freight,
    }


def merge_supplier_fields(df: pd.DataFrame, supplier_df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or supplier_df.empty:
        return df
    merged = df.merge(
        supplier_df[
            ["SkuId", "阿里巴巴采购价(预)", "自定义1688运费金额", "supplier_status"]
        ],
        on="SkuId",
        how="left",
        suffixes=("", "_sup"),
    )
    for col in ("阿里巴巴采购价(预)", "自定义1688运费金额", "supplier_status"):
        sup_col = f"{col}_sup"
        if sup_col in merged.columns:
            merged[col] = merged[col].where(merged[col].notna(), merged[sup_col])
            merged = merged.drop(columns=[sup_col])
    return merged


def enrich_products_with_supplier(
    normalized_products: list[dict],
    config: dict,
) -> tuple[list[dict], dict]:
    """Best-effort enrichment using mapping + defaults.

    No live crawl is performed in this minimal implementation; it prepares
    deterministic supplier fields from mapping/default values and marks
    manual_required when category keyword does not match.
    """
    supplier_cfg = config.get("supplier1688", {})
    if not supplier_cfg.get("enabled", False):
        return normalized_products, {
            "mapping_total": 0,
            "mapping_hit": 0,
            "manual_required": 0,
        }

    mapping_path = supplier_cfg.get("mapping_file", "supplier_mapping.csv")
    if not os.path.isabs(mapping_path):
        mapping_path = os.path.join(os.path.dirname(__file__), mapping_path)
    mapping = load_mapping(mapping_path)

    defaults = supplier_cfg.get("default_values", {})
    global_freight = defaults.get("global_freight", 8.0)
    category_defaults = defaults.get("category_freight", {})

    stats = {
        "mapping_total": 0,
        "mapping_hit": 0,
        "manual_required": 0,
        "mapping_miss": 0,
    }
    enriched: list[dict] = []

    for row in normalized_products:
        row = dict(row)
        sku = row.get("SkuId")
        if not isinstance(sku, int):
            enriched.append(row)
            continue
        m = mapping.get(sku)
        if not m:
            enriched.append(row)
            continue

        stats["mapping_total"] += 1
        target = resolve_supplier_target(m, supplier_cfg)
        if not target or target.get("type") != "keyword":
            stats["mapping_miss"] += 1
            row["supplier_status"] = "mapping_miss"
            enriched.append(row)
            continue

        stats["mapping_hit"] += 1
        category3 = str(row.get("三级类目") or "")
        if not is_category_match(category3, m, supplier_cfg):
            # Category-based matching failed; requires manual intervention.
            stats["manual_required"] += 1
            row["supplier_status"] = "manual_required"
            enriched.append(row)
            continue

        # Placeholder real-field fill: uses defaults until crawler is attached.
        category1 = str(row.get("一级类目") or "")
        mapped_item = dict(m)
        # Prefer CSV freight_est over global/category default
        if mapped_item.get("freight_est") is None:
            mapped_item["freight_est"] = category_defaults.get(category1, global_freight)
        supplier_fields = fetch_supplier_fields(mapped_item, supplier_cfg)
        row["阿里巴巴采购价(预)"] = (
            row.get("阿里巴巴采购价(预)")
            if row.get("阿里巴巴采购价(预)") is not None
            else supplier_fields.get("purchase_price")
        )
        row["自定义1688运费金额"] = supplier_fields.get("freight_est")
        row["supplier_status"] = "ok"
        enriched.append(row)

    return enriched, stats
