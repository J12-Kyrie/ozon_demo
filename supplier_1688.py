"""1688 supplier enrichment (keyword-first, with live scraping fallback).

Scraping strategy:
- CSV mapping is the primary data source (deterministic, fast)
- If CSV has no purchase_price/freight_est for a mapped SKU, attempt live
  Playwright scraping of 1688 search → detail pages
- Chinese page regex targets: ¥/￥ prices, 运费(freight), 起批(MOQ)
"""

from __future__ import annotations

import asyncio
import csv
import os
import re
import logging
from dataclasses import dataclass
from urllib.parse import quote_plus
import pandas as pd

log = logging.getLogger(__name__)

# --------------- 1688 Chinese page regex ---------------

# Price: ¥123.45 or ￥123.45
_1688_PRICE_RE = re.compile(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)")
# Freight: 运费：¥8.00 or 快递: ￥10
_1688_FREIGHT_RE = re.compile(
    r"(?:运费|快递|物流)[^¥￥\d]{0,10}[¥￥]?\s*(\d+(?:\.\d{1,2})?)"
)
# MOQ / min order: 起批量 ≥2 or 1件起批
_1688_MOQ_RE = re.compile(r"(?:起批|起订|≥)\s*(\d+)")


def _extract_1688_number(text: str, pattern: re.Pattern) -> float | None:
    """Extract the first number from text matching the given pattern."""
    m = pattern.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, TypeError):
        return None


async def scrape_1688_product(keyword: str, config: dict) -> dict | None:
    """Scrape 1688 search for a product matching keyword.

    1688 search requires login — use CDP mode (connect to user's real browser
    where they are already logged in) or fall back to estimator ratios.

    Returns dict with keys: purchase_price, freight_est, status
    or None if scraping fails entirely.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("Playwright not available for 1688 scraping")
        return None

    keyword_enc = quote_plus(keyword)
    search_url = (
        f"https://s.1688.com/selloffer/offer_search.htm?keywords={keyword_enc}"
    )
    timeout_ms = int(config.get("timeout", 30)) * 1000
    mode = config.get("1688_mode", "playwright")

    try:
        async with async_playwright() as p:
            if mode == "cdp":
                cdp_endpoint = config.get("1688_cdp_endpoint", "http://127.0.0.1:9222")
                browser = await p.chromium.connect_over_cdp(cdp_endpoint)
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                close_browser = False
            else:
                browser = await p.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-gpu"]
                )
                ctx = await browser.new_context(
                    viewport={"width": 1280, "height": 800}, locale="zh-CN"
                )
                close_browser = True

            try:
                page = await ctx.new_page()
                page.set_default_timeout(timeout_ms)

                resp = await page.goto(search_url, wait_until="domcontentloaded")
                if resp and resp.status >= 400:
                    log.warning("1688 search HTTP %d for '%s'", resp.status, keyword)
                    return None

                await page.wait_for_timeout(3000)

                # Detect login wall
                if "login.taobao.com" in page.url or "login.1688.com" in page.url:
                    log.info("1688 login wall — use CDP mode (1688_mode: cdp) with logged-in browser")
                    return {"status": "login_required"}

                # Get the first product link from search results
                first_link = await page.query_selector(
                    'a[href*="offer"]:not([href*="search"])'
                )
                if not first_link:
                    log.info("No 1688 results for keyword: %s", keyword)
                    return {"status": "no_results"}

                href = await first_link.get_attribute("href")
                if not href:
                    return {"status": "no_href"}

                if not href.startswith("http"):
                    href = f"https:{href}" if href.startswith("//") else f"https://detail.1688.com{href}"
                await page.goto(href, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)

                text = await page.content()
                purchase_price = _extract_1688_number(text, _1688_PRICE_RE)
                freight_est = _extract_1688_number(text, _1688_FREIGHT_RE)

                if purchase_price is None:
                    return {"status": "price_not_found"}

                return {
                    "purchase_price": purchase_price,
                    "freight_est": freight_est,
                    "status": "ok",
                }

            finally:
                await page.close()
                if close_browser:
                    await browser.close()

    except Exception as e:
        log.warning("1688 scrape failed for '%s': %s", keyword, e)
        return None


def scrape_1688_sync(keyword: str, config: dict) -> dict | None:
    """Synchronous wrapper for scrape_1688_product."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(scrape_1688_product(keyword, config))
            loop.close()
            return result
        return loop.run_until_complete(scrape_1688_product(keyword, config))
    except RuntimeError:
        return asyncio.run(scrape_1688_product(keyword, config))


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
    """Fetch supplier fields: CSV first, live scrape fallback, defaults last."""
    defaults = cfg.get("default_values", {})
    global_freight = defaults.get("global_freight", 8.0)

    purchase_price = mapped_item.get("purchase_price")
    freight_est = mapped_item.get("freight_est")

    # Attempt live 1688 scraping only when explicitly enabled. 1688 often needs
    # a logged-in China network/browser session, so deterministic CSV/defaults
    # are the safe baseline.
    keyword = mapped_item.get("supplier_keyword", "")
    if purchase_price is None and keyword and cfg.get("live_scrape_enabled", False):
        log.info("Attempting live 1688 scrape for keyword: %s", keyword)
        scraped = scrape_1688_sync(keyword, cfg)
        if scraped and scraped.get("status") == "ok":
            purchase_price = scraped.get("purchase_price")
            if scraped.get("freight_est") is not None:
                freight_est = scraped.get("freight_est")

    if freight_est is None:
        freight_est = global_freight
    return {
        "purchase_price": purchase_price,
        "freight_est": freight_est,
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

    Live crawling is opt-in via supplier1688.live_scrape_enabled. The default
    path prepares deterministic supplier fields from mapping/default values and
    marks manual_required when category keyword does not match.
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
