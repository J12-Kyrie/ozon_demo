#!/usr/bin/env python3
"""Collect Ozon HTML fixtures with ground truth extracted via Playwright DOM API.

Connects to Ozon via CDP (or other configured mode), navigates to category/search
URLs, saves raw HTML and Playwright DOM-extracted ground truth to tests/fixtures/.

Usage:
    python scripts/collect_fixtures.py                           # use defaults
    python scripts/collect_fixtures.py --urls URL1 URL2 ...      # specify URLs
    python scripts/collect_fixtures.py --mode cdp --count 3      # CDP mode, 3 pages
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

FIXTURES_DIR = BASE_DIR / "tests" / "fixtures"
CONFIG_PATH = BASE_DIR / "config.yaml"

# Default Ozon category URLs to scrape
DEFAULT_URLS = [
    "https://www.ozon.ru/category/smartfony-15502/",
    "https://www.ozon.ru/category/elektronika-15500/",
    "https://www.ozon.ru/category/noutbuki-15692/",
]


def load_config() -> dict:
    """Load scraper config from config.yaml."""
    if not CONFIG_PATH.exists():
        print(f"Config file not found: {CONFIG_PATH}", file=sys.stderr)
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("scraper", {})


# JavaScript function injected into the browser to extract ground truth from DOM.
# This mirrors what parse_product_card() tries to extract from raw HTML.
#
# DOM_EXTRACT_JS has been hardened across four dimensions to provide trustworthy
# ground truth for both synthetic and real Ozon pages:
#   1. Price: class-based disambiguation (crossed-out / original-price CSS classes)
#      with sorted-text fallback for pages without class markers.
#   2. Rating: element-level detection (star/rating container selectors) with
#      review-count extraction, falling back to text regex.
#   3. Seller: text-based fallback via seller/merchant/shop-name CSS classes when
#      the data-seller attribute is absent.
#   4. Title: longest-candidate heuristic (after skip-pattern filtering) instead of
#      naive first-match, preventing short non-title spans from being selected.
DOM_EXTRACT_JS = """\
() => {
  const cards = document.querySelectorAll('[class*="tile-root"]');
  const results = [];
  cards.forEach(card => {
    // ── SKU ──────────────────────────────────────────────────────────
    let sku_id = null;
    const link = card.querySelector('a[href*="/product/"]');
    if (link) {
      const href = link.getAttribute('href');
      const match = href.match(/-(\\d{5,})\\//);
      if (match) sku_id = match[1];
    }
    // Fallback: data-sku attribute
    if (!sku_id) {
      const skuEl = card.querySelector('[data-sku]');
      if (skuEl) sku_id = skuEl.getAttribute('data-sku');
    }

    // ── Title (longest-candidate heuristic) ──────────────────────────
    let title = null;
    const spans = Array.from(card.querySelectorAll('span'))
      .map(el => el.textContent.trim())
      .filter(t => t.length > 10);
    const skipPattern = /(?:₽|баллов|бонус|шт\\s*осталось|^[\\d\\s.,]+$)/i;
    const candidates = [];
    for (const t of spans) {
      if (!skipPattern.test(t)) {
        candidates.push(t);
      }
    }
    // Select the LONGEST candidate to avoid short labels (delivery, stock, badges)
    if (candidates.length > 0) {
      title = candidates.reduce((a, b) => a.length >= b.length ? a : b);
    }

    // ── Prices (class-based disambiguation with sorted fallback) ─────
    let price_current = null;
    let price_original = null;

    // Find all span/div elements that contain a ₽ price
    const priceSpans = Array.from(card.querySelectorAll('span, div')).filter(el => {
      const text = el.textContent.trim();
      return /₽/.test(text) && /\\d/.test(text);
    });

    // CSS selectors that identify a crossed-out / original (non-discounted) price
    const originalPriceSelectors = [
      '[class*="price_original"]', '[class*="strikethrough"]',
      '[class*="old-price"]', '[class*="old_price"]',
      '[class*="cross"]', '[class*="discount"]', '[class*="original"]',
      's', 'del'
    ];

    let allPrices = [];

    priceSpans.forEach(el => {
      const text = el.textContent || '';
      const priceMatch = text.match(/([\\d\\s\\u2009\\u00A0]+)\\s*₽/);
      if (priceMatch) {
        const cleaned = priceMatch[1].replace(/[\\s\\u2009\\u00A0]/g, '');
        const num = parseFloat(cleaned);
        if (!isNaN(num) && num > 1) {
          let isOriginal = false;
          for (const sel of originalPriceSelectors) {
            try {
              if (el.matches(sel) || el.closest(sel)) {
                isOriginal = true;
                break;
              }
            } catch (_) { /* invalid selector, skip */ }
          }
          allPrices.push({ value: num, isOriginal: isOriginal });
        }
      }
    });

    // Deduplicate by value
    const seen = new Set();
    const uniquePrices = [];
    for (const p of allPrices) {
      if (!seen.has(p.value)) {
        seen.add(p.value);
        uniquePrices.push(p);
      }
    }

    const originalCandidates = uniquePrices.filter(p => p.isOriginal);
    const regularCandidates = uniquePrices.filter(p => !p.isOriginal);

    if (originalCandidates.length > 0) {
      price_original = originalCandidates[0].value;
    }
    if (regularCandidates.length > 0) {
      // On Ozon, the current (discount) price is lower than the original
      price_current = regularCandidates.reduce(
        (a, b) => a.value < b.value ? a : b
      ).value;
    }

    // Fallback: text-pattern extraction when class-based finds nothing
    if (price_current === null && price_original === null) {
      const allText = card.textContent || '';
      const pricePattern = /([\\d\\s\\u2009\\u00A0]+)\\s*₽/g;
      let priceMatch;
      const prices = [];
      while ((priceMatch = pricePattern.exec(allText)) !== null) {
        const cleaned = priceMatch[1].replace(/[\\s\\u2009\\u00A0]/g, '');
        const num = parseFloat(cleaned);
        if (!isNaN(num) && num > 1) prices.push(num);
      }
      if (prices.length > 0) {
        // Sort ascending: current = smallest, original = second-smallest
        prices.sort((a, b) => a - b);
        price_current = prices[0];
        if (prices.length > 1) price_original = prices[1];
      }
    } else if (price_current === null && price_original !== null) {
      // Edge case: only original found via class, try text for current
      const allText = card.textContent || '';
      const pricePattern = /([\\d\\s\\u2009\\u00A0]+)\\s*₽/g;
      let priceMatch;
      const prices = [];
      while ((priceMatch = pricePattern.exec(allText)) !== null) {
        const cleaned = priceMatch[1].replace(/[\\s\\u2009\\u00A0]/g, '');
        const num = parseFloat(cleaned);
        if (!isNaN(num) && num > 1) prices.push(num);
      }
      if (prices.length > 0) {
        prices.sort((a, b) => a - b);
        // Pick the smallest price that is NOT the original
        for (const p of prices) {
          if (Math.abs(p - price_original) > 0.01) {
            price_current = p;
            break;
          }
        }
      }
    }

    // ── Rating (element-level detection with text-regex fallback) ────
    let rating = null;

    // Query for star-rating / review container elements
    const ratingSelectors = [
      '[class*="star"]', '[class*="rating"]',
      '[aria-label*="звезд"]', '[data-rating]',
      '[class*="review"]', '[class*="comment"]'
    ];

    for (const sel of ratingSelectors) {
      const ratingEl = card.querySelector(sel);
      if (!ratingEl) continue;

      // data-rating attribute (explicit, most reliable)
      const dataRating = ratingEl.getAttribute('data-rating');
      if (dataRating) {
        const val = parseFloat(dataRating);
        if (!isNaN(val) && val >= 1.0 && val <= 5.0) {
          rating = val;
          break;
        }
      }
      // aria-label (e.g. "Рейтинг 4.5 звезд")
      const ariaLabel = ratingEl.getAttribute('aria-label');
      if (ariaLabel) {
        const match = ariaLabel.match(/(\\d+\\.?\\d*)/);
        if (match) {
          const val = parseFloat(match[1]);
          if (val >= 1.0 && val <= 5.0) {
            rating = val;
            break;
          }
        }
      }
      // Text content within the element itself
      const text = ratingEl.textContent.trim();
      const match = text.match(/(\\d+\\.\\d+)/);
      if (match) {
        const val = parseFloat(match[1]);
        if (val >= 1.0 && val <= 5.0) {
          rating = val;
          break;
        }
      }
    }

    // Fallback to full-card text regex if no rating element found
    if (rating === null) {
      const allText = card.textContent || '';
      const ratingMatch = allText.match(/(\\d+\\.\\d+)/);
      if (ratingMatch) {
        const val = parseFloat(ratingMatch[1]);
        if (val >= 1.0 && val <= 5.0) rating = val;
      }
    }

    // ── Seller (data-seller attribute + text-based CSS fallback) ─────
    let seller = null;
    const sellerEl = card.querySelector('[data-seller]');
    if (sellerEl) {
      seller = sellerEl.getAttribute('data-seller');
    }

    // Text-based fallback when data-seller attribute is absent
    if (!seller) {
      const sellerSelectors = [
        '[class*="seller"]', '[class*="merchant"]',
        '[class*="shop-name"]', '[class*="store-name"]',
        '[class*="shop"]', '[class*="brand"]'
      ];
      for (const sel of sellerSelectors) {
        const el = card.querySelector(sel);
        if (el) {
          const text = el.textContent.trim();
          if (text && text.length > 1) {
            seller = text;
            break;
          }
        }
      }
    }

    results.push({
      sku_id: sku_id,
      title: title,
      price_current: price_current,
      price_original: price_original,
      rating: rating,
      seller: seller
    });
  });
  return results.filter(r => r.sku_id !== null);
}
"""


async def collect_from_url(
    page, url: str, fixture_index: int
) -> tuple[str, list[dict]] | None:
    """Navigate to a URL and collect raw HTML + ground truth.

    Returns (html, truth_list) or None on failure.
    """
    print(f"  Navigating to: {url}")
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"  Error navigating to {url}: {e}", file=sys.stderr)
        return None

    if resp and resp.status >= 400:
        print(f"  HTTP {resp.status} for {url}, skipping", file=sys.stderr)
        return None

    # Wait for product cards to render
    await page.wait_for_timeout(5000)

    # Scroll to trigger lazy loading
    for _ in range(4):
        await page.evaluate("window.scrollBy(0, window.innerHeight)")
        await page.wait_for_timeout(1000)

    # Extract ground truth via DOM
    truth_list = await page.evaluate(DOM_EXTRACT_JS)
    if not truth_list:
        print(f"  No product cards found on {url}, skipping", file=sys.stderr)
        return None

    # Get full page HTML
    html = await page.content()

    print(f"  Extracted {len(truth_list)} cards")
    return html, truth_list


async def main_async(args):
    """Async main: connect to browser and collect fixtures."""
    from playwright.async_api import async_playwright

    config = load_config()
    mode = args.mode or config.get("mode", "cdp")
    urls = args.urls if args.urls else DEFAULT_URLS
    max_pages = args.count

    fixture_index = 0

    # Ensure fixtures directory exists
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        # Connect or launch based on mode
        if mode == "cdp":
            endpoint = config.get("cdp_endpoint", "http://127.0.0.1:9222")
            print(f"Connecting to browser via CDP: {endpoint}")
            try:
                browser = await p.chromium.connect_over_cdp(endpoint)
            except Exception as e:
                print(
                    f"Failed to connect via CDP at {endpoint}: {e}\n"
                    f"Make sure Chrome is running with --remote-debugging-port=9222\n"
                    f"Try: --mode playwright for headless Chromium instead.",
                    file=sys.stderr,
                )
                return 1
            context = (
                browser.contexts[0]
                if browser.contexts
                else await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    locale="ru-RU",
                    timezone_id="Europe/Moscow",
                )
            )
            close_browser = False  # Don't close user's real browser
        elif mode == "playwright":
            print("Launching headless Chromium")
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu"],
            )
            proxy_url = config.get("proxy", "").strip()
            context_kwargs = {
                "viewport": {"width": 1280, "height": 800},
                "locale": "ru-RU",
                "timezone_id": "Europe/Moscow",
            }
            if proxy_url:
                context_kwargs["proxy"] = {"server": proxy_url}
            user_agent = config.get("user_agent", "")
            if user_agent:
                context_kwargs["user_agent"] = user_agent
            context = await browser.new_context(**context_kwargs)
            close_browser = True
        else:
            print(f"Unsupported mode: {mode}", file=sys.stderr)
            return 1

        try:
            page = await context.new_page()
            page.set_default_timeout(config.get("timeout", 30) * 1000)

            for url in urls:
                for page_num in range(1, max_pages + 1):
                    target_url = url
                    if page_num > 1:
                        # Append pagination parameter
                        sep = "&" if "?" in url else "?"
                        target_url = f"{url}{sep}page={page_num}"

                    print(f"Page {page_num}/{max_pages}")
                    try:
                        result = await collect_from_url(
                            page, target_url, fixture_index + 1
                        )
                    except Exception as e:
                        print(
                            f"  Collection failed for {target_url}: {e}",
                            file=sys.stderr,
                        )
                        continue

                    if result is None:
                        continue

                    html, truth_list = result

                    # Write fixture files
                    fixture_index += 1
                    html_path = FIXTURES_DIR / f"fixture_{fixture_index}.html"
                    truth_path = (
                        FIXTURES_DIR / f"fixture_{fixture_index}_truth.json"
                    )

                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(html)

                    with open(truth_path, "w", encoding="utf-8") as f:
                        json.dump(
                            truth_list, f, ensure_ascii=False, indent=2
                        )

                    print(
                        f"  Saved: {html_path.name} ({len(truth_list)} cards)"
                    )

            print(
                f"\nDone. Collected {fixture_index} fixture(s) in {FIXTURES_DIR}"
            )

        finally:
            await page.close()
            if close_browser:
                await browser.close()

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Collect Ozon HTML fixtures with Playwright DOM ground truth"
    )
    parser.add_argument(
        "--mode",
        choices=["cdp", "playwright"],
        default=None,
        help="Browser connection mode (default: from config.yaml scraper.mode)",
    )
    parser.add_argument(
        "--urls",
        nargs="+",
        default=None,
        help="Ozon category/search URLs to scrape",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Max pages per URL (default: 3)",
    )
    args = parser.parse_args()

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
