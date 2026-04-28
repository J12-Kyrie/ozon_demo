#!/usr/bin/env python3
"""Scrape 1688 product data from an already-open browser tab via CDP.

Usage:
    1. Open Edge with --remote-debugging-port=9222
    2. Log into https://www.1688.com/
    3. Manually navigate to a product detail page
    4. Run: python scripts/scrape_1688_tab.py [--keyword KEYWORD]

The script reads product data (price, freight, MOQ, supplier) from the
currently active 1688 detail tab and outputs JSON.
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

PRICE_RE = re.compile(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)")
FREIGHT_RE = re.compile(r"(?:运费|快递|物流)[^¥￥\d]{0,20}[¥￥]?\s*(\d+(?:\.\d{1,2})?)")
MOQ_RE = re.compile(r"(?:起批|起订|≥)\s*(\d+)")
SUPPLIER_RE = re.compile(r"(?:供应商|公司名称|厂家|店铺)[:：]?\s*([^\s<]{2,50})")
TITLE_RE = re.compile(r"<title>([^<]+)</title>")


async def find_1688_detail_tab(browser) -> tuple:
    """Find a 1688 product detail tab. Returns (page, url) or (None, None)."""
    for pg in browser.contexts[0].pages if browser.contexts else []:
        url = pg.url or ""
        if "detail.1688.com/offer/" in url:
            return pg, url
    return None, None


async def scrape_current_tab(page) -> dict:
    """Extract product data from the current 1688 detail page."""
    try:
        await page.wait_for_timeout(1000)
        html = await page.content()
    except Exception as e:
        return {"error": f"Failed to read page: {e}"}

    # Title
    title = None
    tm = TITLE_RE.search(html)
    if tm:
        title = tm.group(1).strip()

    # Price - find all ¥ prices, take the first significant one
    prices = [float(m) for m in PRICE_RE.findall(html) if float(m) > 0.5]
    purchase_price = prices[0] if prices else None
    price_range = f"{min(prices)}-{max(prices)}" if len(prices) > 1 else None

    # Freight
    freight_match = FREIGHT_RE.search(html)
    freight_est = float(freight_match.group(1)) if freight_match else None

    # MOQ
    moq_match = MOQ_RE.search(html)
    moq = int(moq_match.group(1)) if moq_match else None

    # Supplier name
    supplier_match = SUPPLIER_RE.search(html)
    supplier_name = supplier_match.group(1).strip() if supplier_match else None

    # Image count / SKU count (rough)
    sku_images = len(re.findall(r'class="[^"]*tab-trigger[^"]*"', html))

    result = {
        "title": title,
        "purchase_price": purchase_price,
        "price_range": price_range,
        "freight_est": freight_est,
        "moq": moq,
        "supplier_name": supplier_name,
        "sku_count_hint": sku_images if sku_images > 0 else None,
        "status": "ok" if purchase_price else "price_not_found",
    }
    return result


async def search_and_open(page, keyword: str) -> str | None:
    """Search 1688 for keyword and open first result detail page."""
    from urllib.parse import quote_plus

    search_url = f"https://s.1688.com/selloffer/offer_search.htm?keywords={quote_plus(keyword)}"
    await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
    await page.wait_for_timeout(3000)

    html = await page.content()
    if "login" in page.url.lower() or "captcha" in html.lower():
        print("1688 requires login/captcha — manually open a product detail page instead")
        return None

    # Try to find product links
    detail_links = re.findall(r'href="(https?://detail\.1688\.com/offer/\d+\.html)"', html)
    if detail_links:
        await page.goto(detail_links[0], wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)
        return detail_links[0]

    # Fallback: try offer paths
    offer_links = re.findall(r'href="(/offer/\d+\.html)"', html)
    if offer_links:
        url = f"https://detail.1688.com{offer_links[0]}"
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)
        return url

    print("No product links found on search page")
    return None


async def main_async(args):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        cdp = args.cdp or "http://127.0.0.1:9222"
        try:
            browser = await p.chromium.connect_over_cdp(cdp)
        except Exception as e:
            print(f"Failed to connect CDP at {cdp}: {e}", file=sys.stderr)
            print("Start Edge with: --remote-debugging-port=9222", file=sys.stderr)
            return 1

        # Strategy 1: Find existing 1688 detail tab
        page, url = await find_1688_detail_tab(browser)
        if page:
            print(f"Found 1688 detail tab: {url}")
        else:
            # Strategy 2: Create new page and search
            page = await browser.contexts[0].new_page()
            if args.keyword:
                opened = await search_and_open(page, args.keyword)
                if not opened:
                    print("Manual intervention needed — open a 1688 product in browser")
                    return 1
            else:
                print("No 1688 detail tab found. Use --keyword to search or open manually.")
                return 1

        result = await scrape_current_tab(page)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if not args.keep_open:
            await page.close()

    return 0


def main():
    parser = argparse.ArgumentParser(description="Scrape 1688 product from open browser tab")
    parser.add_argument("--cdp", default="http://127.0.0.1:9222", help="CDP endpoint")
    parser.add_argument("--keyword", help="Search keyword for 1688")
    parser.add_argument("--keep-open", action="store_true", help="Don't close tab after scraping")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
