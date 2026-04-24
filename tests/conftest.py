"""Shared test fixtures — sample Ozon HTML snippets."""

import time

import pytest
import requests
from playwright.sync_api import sync_playwright


@pytest.fixture
def sample_product_card_html():
    """Realistic Ozon product card HTML (2026 structure)."""
    return """
    <div data-index="0" class="tile-root gp3_20 i8h_20" style="background-color:var(--layerFloor1);">
      <a data-prerender="true" target="_blank" href="/product/foundation-liquid-concealer-30ml-3340449279/?at=abc123" rel="noopener" class="tile-clickable-element">
        <div class="pg6_20"><img src="https://ir.ozone.ru/example.jpg"></div>
        <div>
          <span class="tsBody500Medium">162 ₽</span>
          <span class="tsBodyControl400Small">199 ₽</span>
        </div>
        <span class="tsBody400Small">Foundation Liquid Concealer 30ml Premium</span>
      </a>
      <div><span>3.7 42 </span></div>
      <div data-seller="NorthVoyage"></div>
    </div>
    """


@pytest.fixture
def sample_product_card_missing_fields():
    """Product card with missing price and rating."""
    return """
    <div data-index="1" class="tile-root gp3_20" style="background-color:var(--layerFloor1);">
      <a href="/product/unknown-product-9999999999/" class="tile-clickable-element">
        <span class="tsBody400Small">Unknown Product Test Item</span>
      </a>
    </div>
    """


@pytest.fixture
def sample_search_page_html():
    """Full search page with 3 product cards (2026 Ozon format)."""
    card_template = """
    <div data-index="{idx}" class="tile-root gp3_20 i8h_20" style="background-color:var(--layerFloor1);">
      <a href="/product/product-name-{sku}/?at=token" class="tile-clickable-element">
        <div><span class="price">{price} ₽</span></div>
        <span class="tsBody400Small">Product {sku} Test Name Here</span>
      </a>
      <div><span>{rating} {reviews} </span></div>
      <div data-seller="Shop{sku}"></div>
    </div>
    """
    cards = [
        card_template.format(
            idx=0, sku="1111111111", price="100", rating="4.5", reviews="10"
        ),
        card_template.format(
            idx=1, sku="2222222222", price="200", rating="3.8", reviews="25"
        ),
        card_template.format(
            idx=2, sku="3333333333", price="350", rating="4.9", reviews="99"
        ),
    ]
    return f"""
    <html><body>
    <div class="search-page">
      <div id="paginatorContent">
        {"".join(cards)}
      </div>
    </div>
    </body></html>
    """


@pytest.fixture
def sample_config():
    """Config dict with scraper section."""
    return {
        "filters": {
            "min_monthly_sales": 2,
            "min_gross_margin": 30.0,
            "max_competitors": 20,
            "max_purchase_price": 200.0,
            "min_selling_price": 50.0,
            "categories": [],
        },
        "scoring": {
            "gross_margin": 0.30,
            "monthly_sales": 0.25,
            "conversion_rate": 0.25,
            "low_competition": 0.20,
        },
        "output": {"top_n": 20},
        "scraper": {
            "mode": "playwright",
            "delay_between_pages": 3.0,
            "delay_between_requests": 1.5,
            "max_pages": 5,
            "timeout": 30,
            "headless": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    }


# --------------- Playwright E2E fixtures ---------------

BASE_URL = "http://localhost:5001"


@pytest.fixture(scope="session", autouse=True)
def ensure_data():
    """Ensure the app has data to work with. Seeds via demo scrape if needed."""
    try:
        resp = requests.get(f"{BASE_URL}/api/health", timeout=10)
        health = resp.json()
    except requests.RequestException:
        # Unit tests can run without a live Flask server.
        return
    if health.get("products_loaded", 0) == 0:
        # Trigger demo scrape to seed data
        requests.post(f"{BASE_URL}/api/scrape", json={"demo": True}, timeout=10)
        # Poll until done (max 30s)
        for _ in range(60):
            status = requests.get(f"{BASE_URL}/api/scrape/status", timeout=5).json()
            if not status.get("running", False):
                break
            time.sleep(0.5)


@pytest.fixture(scope="session")
def browser_instance():
    """Session-scoped Playwright browser."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu"],
        )
        yield browser
        browser.close()


@pytest.fixture
def page(browser_instance, ensure_data):
    """Function-scoped page navigated to app."""
    context = browser_instance.new_context(viewport={"width": 1280, "height": 800})
    pg = context.new_page()
    pg.goto(BASE_URL, wait_until="networkidle", timeout=60000)
    yield pg
    context.close()
