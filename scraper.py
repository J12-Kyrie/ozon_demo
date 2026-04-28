"""Playwright-based Ozon scraper with access-block detection and demo mode.

Supports three scraping modes:
  - playwright: Default headless Chromium with optional proxy tunnel
  - cdp: Connect to user's real browser via Chrome DevTools Protocol
  - camoufox: Anti-detection Firefox (requires `pip install camoufox`)
"""

import asyncio
import base64
import html as html_lib
import os
import json
import random
import re
from urllib.parse import urljoin
from urllib.parse import urlparse
from parser import parse_search_page, normalize_product

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_DATA_PATH = os.path.join(BASE_DIR, "demo_products.json")


class OzonBlockedError(Exception):
    """Raised when Ozon blocks access (IP restriction / anti-bot)."""

    pass


# --------------- demo mode ---------------


def _generate_demo_products(count=15):
    """Generate realistic demo products for testing without Ozon access."""
    categories = [
        ("电子产品", "智能手机"),
        ("电子产品", "耳机"),
        ("家用电器", "吸尘器"),
        ("美容和卫生", "粉底液"),
        ("小百货和配饰", "伞"),
        ("宠物用品", "猫抓柱"),
        ("运动与休闲", "瑜伽垫"),
        ("建筑和装修", "灯具"),
        ("儿童用品", "积木"),
    ]
    sellers = [
        "NorthVoyage",
        "xingqi6YI",
        "jiuliqi",
        "BestShop",
        "MegaStore",
        "TopGoods",
    ]
    products = []

    for i in range(count):
        sku = str(random.randint(3300000000, 3500000000))
        cat1, cat3 = categories[i % len(categories)]
        price = round(random.uniform(80, 800), 0)
        products.append(
            {
                "sku_id": sku,
                "title": f"Demo Product {cat3} #{i + 1}",
                "price": price,
                "original_price": round(price * random.uniform(1.05, 1.3), 0),
                "rating": round(random.uniform(3.0, 5.0), 1),
                "reviews": random.randint(5, 500),
                "seller": sellers[i % len(sellers)],
                "url": f"/product/{sku}",
                "_demo_category1": cat1,
                "_demo_category3": cat3,
            }
        )
    return products


def _normalize_demo_product(raw: dict) -> dict:
    """Extended normalize for demo products — fills in category fields."""
    result = normalize_product(raw)
    if raw.get("_demo_category1"):
        result["一级类目"] = raw["_demo_category1"]
    if raw.get("_demo_category3"):
        result["三级类目"] = raw["_demo_category3"]
    result["data_source"] = "demo"
    return result


def run_demo_scrape(progress_callback=None) -> list[dict]:
    """Simulate a scrape with demo data. No network access needed."""
    import time

    products = _generate_demo_products(15)

    for i in range(3):
        time.sleep(0.5)
        batch = products[i * 5 : (i + 1) * 5]
        if progress_callback:
            progress_callback(
                {
                    "current_page": i + 1,
                    "total_pages": 3,
                    "products_found": min((i + 1) * 5, len(products)),
                    "new_on_page": len(batch),
                }
            )

    return products


# --------------- local proxy tunnel ---------------


async def _relay(reader, writer):
    """Relay bytes between two streams until EOF."""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (asyncio.CancelledError, ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _start_proxy_tunnel(upstream_url: str):
    """Start a local HTTP proxy that forwards to upstream with auth injected.

    Returns (server, local_port). Caller must call server.close() when done.
    """
    parsed = urlparse(upstream_url)
    up_host = parsed.hostname
    up_port = parsed.port
    creds = base64.b64encode(f"{parsed.username}:{parsed.password}".encode()).decode()

    async def handle_client(client_r, client_w):
        try:
            req_line = await asyncio.wait_for(client_r.readline(), timeout=10)
            if not req_line:
                client_w.close()
                return

            headers_raw = b""
            while True:
                line = await asyncio.wait_for(client_r.readline(), timeout=10)
                if line == b"\r\n" or not line:
                    break
                headers_raw += line

            up_r, up_w = await asyncio.open_connection(up_host, up_port)

            up_w.write(req_line)
            up_w.write(f"Proxy-Authorization: Basic {creds}\r\n".encode())
            up_w.write(headers_raw)
            up_w.write(b"\r\n")
            await up_w.drain()

            resp_line = await asyncio.wait_for(up_r.readline(), timeout=30)
            resp_headers = resp_line
            while True:
                line = await asyncio.wait_for(up_r.readline(), timeout=10)
                resp_headers += line
                if line == b"\r\n" or not line:
                    break

            client_w.write(resp_headers)
            await client_w.drain()

            if b"200" in resp_line:
                await asyncio.gather(
                    _relay(client_r, up_w),
                    _relay(up_r, client_w),
                )
            else:
                client_w.close()
                up_w.close()

        except Exception:
            try:
                client_w.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    local_port = server.sockets[0].getsockname()[1]
    return server, local_port


# --------------- shared scraping logic ---------------


def _extract_page_categories(page_html: str) -> tuple[str | None, str | None]:
    """Extract category hierarchy from JSON-LD BreadcrumbList or state-breadCrumbs.

    Returns (category_level1, category_level3).
    Tries JSON-LD first, then state-breadCrumbs data-state, then URL path fallback.
    """
    # Source 1: JSON-LD BreadcrumbList
    for m in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        page_html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(html_lib.unescape(m.group(1)))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "BreadcrumbList":
            items = data.get("itemListElement", [])
            if len(items) >= 1:
                return items[0].get("name"), (
                    items[-1].get("name") if len(items) > 1 else None
                )

    # Source 2: state-breadCrumbs data-state attribute
    bc_match = re.search(
        r'(?:id|data-widget)="[^"]*breadCrumbs[^"]*"[^>]*data-state="([^"]+)"',
        page_html, re.IGNORECASE,
    )
    if bc_match:
        try:
            state = json.loads(html_lib.unescape(bc_match.group(1)))
            crumbs = state.get("breadcrumbs", [])
            if len(crumbs) >= 1:
                return crumbs[0].get("text"), (
                    crumbs[-1].get("text") if len(crumbs) > 1 else None
                )
        except (json.JSONDecodeError, TypeError):
            pass

    return None, None


async def _extract_products(page, url, config, progress_callback) -> list[dict]:
    """Navigate to URL, detect blocks, paginate, and extract products.

    This is the shared core used by all scrape modes.
    """
    delay_pages = config.get("delay_between_pages", 3.0)
    max_pages = config.get("max_pages", 5)

    all_products = []
    seen_skus = set()

    resp = await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # --- Access block detection ---
    status = resp.status if resp else 0
    html = await page.content()
    title = ""
    try:
        title = await page.title()
    except Exception:
        pass

    blocked_signals = [
        status == 403,
        "доступ ограничен" in html.lower(),
        "antibot" in title.lower(),
        "captcha" in html.lower(),
        "blocked" in html.lower() and "access" in html.lower(),
    ]
    if any(blocked_signals):
        raise OzonBlockedError(
            f"Ozon 访问受限 (HTTP {status})。"
            f"可能原因：当前 IP 不在俄罗斯 / VPN 被检测 / IP 被封禁。"
            f"建议：1) 使用俄罗斯 IP 代理  2) 使用 Demo 模式测试功能"
        )

    # --- Extract page-level categories from first page (before pagination) ---
    page1_html = html
    cat_l1, cat_l3 = _extract_page_categories(page1_html)

    # --- Scrape pages ---
    for page_num in range(1, max_pages + 1):
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(800)

        html = await page.content()
        cards = collect_list_products(parse_search_page(html))

        new_count = 0
        for card in cards:
            sku = card.get("sku_id")
            if sku and sku not in seen_skus:
                seen_skus.add(sku)
                all_products.append(card)
                new_count += 1

        if progress_callback:
            progress_callback(
                {
                    "current_page": page_num,
                    "total_pages": max_pages,
                    "products_found": len(all_products),
                    "new_on_page": new_count,
                }
            )

        if new_count == 0:
            break

        if page_num < max_pages:
            next_btn = page.locator('a[data-widget="megaPaginator"] >> text="Дальше"')
            if await next_btn.count() > 0:
                await next_btn.first.click()
                await page.wait_for_timeout(int(delay_pages * 1000))
            else:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(int(delay_pages * 1000))

    # Inject page-level categories (captured from page 1 before pagination)
    if cat_l1 or cat_l3:
        for card in all_products:
            if cat_l1:
                card["list_category_level1"] = cat_l1
            if cat_l3:
                card["list_category_level3"] = cat_l3

    return await enrich_products_with_detail(all_products, config, page)


def collect_list_products(cards: list[dict]) -> list[dict]:
    """Normalize list-card output structure for downstream enrichment."""
    result = []
    for card in cards:
        row = dict(card)
        row["list_category_level1"] = row.get("list_category_level1")
        row["list_category_level3"] = row.get("list_category_level3")
        row["list_monthly_sales"] = row.get("list_monthly_sales")
        row["list_competitor_count"] = row.get("list_competitor_count")
        result.append(row)
    return result


def extract_detail_fields(page_html: str) -> dict:
    """Extract key fields from Ozon detail page HTML.

    Uses JSON-LD and state-breadCrumbs for categories (not arbitrary <a> tags
    which pick up unrelated recommendation links).
    """
    lower = page_html.lower()
    # Categories: JSON-LD or state-breadCrumbs only (never random <a> tags)
    level1, level3 = _extract_page_categories(page_html)

    comp = None
    comp_match = re.search(
        r"(?:跟卖|продавц|sellers?)[^\d]{0,8}(\d+)", lower, re.IGNORECASE
    )
    if comp_match:
        comp = int(comp_match.group(1))

    sales_hint = None
    sales_match = re.search(
        r"(?:月销|购买|sold)[^\d]{0,8}(\d+)", page_html, re.IGNORECASE
    )
    if sales_match:
        sales_hint = int(sales_match.group(1))

    return {
        "detail_category_level1": level1,
        "detail_category_level3": level3,
        "detail_competitor_count": comp,
        "detail_monthly_sales": sales_hint,
    }


async def enrich_products_with_detail(
    products: list[dict], config: dict, page
) -> list[dict]:
    """Best-effort TopK detail scrape and merge by sku_id."""
    cfg = config or {}
    enabled = cfg.get("detail_enrich_enabled", False)
    top_k = int(cfg.get("detail_top_k", 30))
    timeout_ms = int(cfg.get("detail_timeout", 30) * 1000)
    if not enabled or not products:
        return products

    enriched = []
    failed_count = 0
    for idx, item in enumerate(products):
        merged = dict(item)
        if idx < top_k and item.get("url"):
            detail_url = urljoin("https://www.ozon.ru", str(item["url"]))
            try:
                await page.goto(
                    detail_url, wait_until="domcontentloaded", timeout=timeout_ms
                )
                await page.wait_for_timeout(800)
                html = await page.content()
                merged.update(extract_detail_fields(html))
            except Exception:
                failed_count += 1
        enriched.append(merged)

    if failed_count:
        for row in enriched:
            row["detail_enrich_failed_count"] = failed_count
    return enriched


# --------------- mode: playwright ---------------


async def _scrape_via_playwright(url, config, progress_callback):
    """Standard Playwright Chromium with optional proxy tunnel."""
    from playwright.async_api import async_playwright

    headless = config.get("headless", True)
    user_agent = config.get("user_agent", "")
    proxy_url = config.get("proxy", "").strip()
    timeout_ms = config.get("timeout", 30) * 1000

    tunnel_server = None
    local_proxy = None
    if proxy_url:
        parsed = urlparse(proxy_url)
        if parsed.username:
            tunnel_server, local_port = await _start_proxy_tunnel(proxy_url)
            local_proxy = f"http://127.0.0.1:{local_port}"
        else:
            local_proxy = proxy_url

    try:
        async with async_playwright() as p:
            launch_opts = {
                "headless": headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            }
            if local_proxy:
                launch_opts["proxy"] = {"server": local_proxy}

            browser = await p.chromium.launch(**launch_opts)
            context = await browser.new_context(
                user_agent=user_agent if user_agent else None,
                viewport={"width": 1280, "height": 800},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                ignore_https_errors=True,
            )

            try:
                from playwright_stealth import Stealth

                await Stealth().apply_stealth_async(context)
            except ImportError:
                pass

            page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            try:
                return await _extract_products(page, url, config, progress_callback)
            except OzonBlockedError:
                raise
            except Exception as e:
                if progress_callback:
                    progress_callback(
                        {
                            "current_page": 0,
                            "total_pages": config.get("max_pages", 5),
                            "products_found": 0,
                            "error": str(e),
                        }
                    )
                return []
            finally:
                await browser.close()
    finally:
        if tunnel_server:
            tunnel_server.close()
            await tunnel_server.wait_closed()


# --------------- mode: cdp ---------------


async def _scrape_via_cdp(url, config, progress_callback):
    """Connect to user's real browser via Chrome DevTools Protocol.

    Launch your browser with: --remote-debugging-port=9222
    The real browser's fingerprint, cookies, and session bypass anti-bot detection.
    """
    from playwright.async_api import async_playwright

    endpoint = config.get("cdp_endpoint", "http://127.0.0.1:9222")
    timeout_ms = config.get("timeout", 30) * 1000

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(endpoint)

        # Use the browser's default context (has user's cookies/session)
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )

        page = await context.new_page()
        page.set_default_timeout(timeout_ms)

        try:
            return await _extract_products(page, url, config, progress_callback)
        except OzonBlockedError:
            raise
        except Exception as e:
            if progress_callback:
                progress_callback(
                    {
                        "current_page": 0,
                        "total_pages": config.get("max_pages", 5),
                        "products_found": 0,
                        "error": str(e),
                    }
                )
            return []
        finally:
            await page.close()
            # Do NOT close browser — it's the user's real browser


# --------------- mode: camoufox ---------------


async def _scrape_via_camoufox(url, config, progress_callback):
    """Use camoufox anti-detection Firefox. Install: pip install camoufox && camoufox fetch"""
    from camoufox.async_api import AsyncCamoufox

    headless = config.get("headless", True)
    proxy_url = config.get("proxy", "").strip()
    timeout_ms = config.get("timeout", 30) * 1000

    camoufox_opts = {"headless": headless}
    if proxy_url:
        parsed = urlparse(proxy_url)
        proxy_cfg = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            proxy_cfg["username"] = parsed.username
        if parsed.password:
            proxy_cfg["password"] = parsed.password
        camoufox_opts["proxy"] = proxy_cfg

    async with AsyncCamoufox(**camoufox_opts) as browser:
        page = await browser.new_page()
        page.set_default_timeout(timeout_ms)

        try:
            return await _extract_products(page, url, config, progress_callback)
        except OzonBlockedError:
            raise
        except Exception as e:
            if progress_callback:
                progress_callback(
                    {
                        "current_page": 0,
                        "total_pages": config.get("max_pages", 5),
                        "products_found": 0,
                        "error": str(e),
                    }
                )
            return []
        finally:
            await page.close()


# --------------- public API ---------------


async def scrape_category(url: str, config: dict, progress_callback=None) -> list[dict]:
    """Navigate to Ozon category/search URL, extract products.

    Dispatches to the appropriate scrape mode based on config["mode"]:
      - "playwright" (default): Headless Chromium with optional proxy
      - "cdp": Connect to user's real browser via remote debugging
      - "camoufox": Anti-detection Firefox

    Raises OzonBlockedError if access is restricted.
    """
    mode = config.get("mode", "playwright")

    if mode == "cdp":
        return await _scrape_via_cdp(url, config, progress_callback)
    elif mode == "camoufox":
        return await _scrape_via_camoufox(url, config, progress_callback)
    else:
        return await _scrape_via_playwright(url, config, progress_callback)


def run_scrape(url: str, config: dict, progress_callback=None) -> list[dict]:
    """Synchronous wrapper — safe to call from a threading.Thread."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(scrape_category(url, config, progress_callback))
    finally:
        loop.close()
