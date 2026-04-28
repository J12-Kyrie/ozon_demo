# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Identity

Ozon 选品助手 — an MVP web app that scrapes Ozon (Russian e-commerce) product listings, enriches them with 1688 supplier data and rule-based estimates, then scores/filters products for cross-border e-commerce sellers.

## Commands

```bash
# Virtual environment
source .venv/bin/activate

# Run the Flask app (port 5001)
python main.py

# Run all tests
pytest tests/ -v

# Run specific test files
pytest tests/test_parser.py -v
pytest tests/test_merge.py -v
pytest tests/test_1688.py -v
pytest tests/test_upgrade.py -v

# Run E2E tests (requires Flask server running on port 5001)
pytest tests/test_e2e.py -v

# Format code
ruff format .

# Lint
ruff check .

# Docker
docker compose up --build -d
docker compose exec app pytest tests/ -v
docker compose down
```

## Architecture: Five-Stage Data Pipeline

The app follows a **scrape → parse → enrich → estimate → score** pipeline, all driven by `main.py` which is both the Flask server and the orchestration hub.

### Stage 1: Scrape (`scraper.py`)
- Three modes selected via `config.yaml` → `scraper.mode`: `playwright` (headless Chromium), `cdp` (attach to user's real browser via Chrome DevTools Protocol), `camoufox` (anti-detection Firefox)
- CDP mode is the primary production path — it rides the user's real browser fingerprint/cookies/session to bypass Ozon anti-bot
- Proxy tunnel: when `proxy` URL contains credentials, `_start_proxy_tunnel()` spins up a local proxy that injects `Proxy-Authorization` header
- `_extract_products()` is the shared core: navigates to category/search URL, detects Ozon blocks (HTTP 403, captcha, "доступ ограничен"), paginates through `max_pages`, collects product cards
- Detail enrichment (`enrich_products_with_detail()`): after list scrape, visits TopK product detail pages to extract category, competitor count, and sales hints — controlled by `scraper.detail_enrich_enabled`
- Demo mode (`run_demo_scrape()`): generates 15 synthetic products, no network calls

### Stage 2: Parse (`parser.py`)
- Pure HTML parsing — no browser dependency, uses regex + stdlib `HTMLParser`
- `parse_search_page()` finds Ozon `tile-root` divs and extracts each card
- `parse_product_card()` extracts: SKU (from URL slug or `data-sku` attribute), title, current/original price, rating, review count, seller
- `normalize_product()` maps scraped fields to the app's Chinese column schema — uses `first_non_null()` priority chain: detail page fields > list page fields > None
- `is_missing_value()` treats `None`/NaN/empty-string as missing but **zero is valid** — this is critical for the scoring path

### Stage 3: 1688 Enrichment (`supplier_1688.py`)
- Conservative design: **no live 1688 crawling** — works from a manual CSV mapping (`supplier_mapping.csv`)
- CSV schema: `ozon_sku, supplier_keyword, supplier_url, purchase_price, freight_est, note`
- Keyword-first strategy: `supplier_keyword` is the primary search key; `supplier_url` is fallback only
- Category-based matching: `_keyword_match()` checks if third-level category tokens appear in the supplier keyword
- Statuses: `ok` (matched + enriched), `mapping_miss` (no keyword provided), `manual_required` (category mismatch — needs human)
- Falls back to `default_values` from config for freight when per-SKU data is missing

### Stage 4: Estimate (`estimator.py`)
- Rule-based estimator that fills gaps in scraped data
- `estimate_margin()`: (price - purchase - freight - commission - ads - return_loss) / price × 100
- `estimate_conversion()`: weighted score from rating, reviews, price competitiveness, and competition
- `estimate_confidence()`: proportion of required fields that are non-null
- Output columns use `_est` suffix (`毛利率_est`, `展示至下单转化率_est`) — **never overwrites real columns**
- Controlled by `estimator.enabled` in config

### Stage 5: Score & Filter (`main.py`)
- `filter_and_score()`: applies configurable filters (min sales, min margin, max competitors, etc.) → filters to `data_source == "scraped"` only → normalizes 4 scoring dimensions to [0,1] → weighted sum → `综合评分`
- **Estimate fallback**: `_with_est_fallback()` uses `combine_first()` — real value preferred, `_est` column as fallback
- `merge_dataframes()`: Excel rows take priority over scraped rows with the same SkuId
- Scrape runs in a **background thread** (`_run_scrape_thread()`), progress reported via shared `SCRAPE_STATUS` dict and SSE stream

## Column Groups

The frontend tabs are backed by three column groups defined in `main.py`:
- `TAB_BASIC_COLS`: SkuId, rating, brand, store, categories, prices, sales, link
- `TAB_TRAFFIC_COLS`: SkuId, conversion rates, page views, impressions
- `TAB_COST_COLS`: SkuId, 1688 purchase price, freight, Ozon logistics, commission, margin, profit

## Key Design Decisions

- **No database**: Data lives in pandas DataFrames, persisted to `scraped_merged.xlsx` between restarts
- **Real-browser CDP for anti-bot**: The app connects to a user-launched Edge browser with Russian proxy, inheriting its fingerprint and session — this is the core anti-detection strategy
- **Scraped-only scoring**: `filter_and_score()` explicitly filters to `data_source == "scraped"` before scoring (Excel rows are excluded from ranking)
- **`stable_fields.yml`**: Tracks which fields can be "reliably scraped" — a field must succeed in `success_streak_required` consecutive scrapes before being marked stable. Estimators should only use stable fields as inputs
- **Conservative 1688 integration**: The `supplier_1688.py` module is a minimal implementation that uses manual CSV mapping — live 1688 crawling is a placeholder (`open_page_1688()` returns `{"status": "ready"}` without actual browser automation)
- **Config-driven**: All thresholds, weights, modes, and API keys live in `config.yaml` — no code changes needed for tuning

## Configuration (`config.yaml`)

- `filters`: threshold values for product filtering (min sales, min margin, max competitors, etc.)
- `scoring`: weights for the 4 scoring dimensions (must sum conceptually, not strictly)
- `output.top_n`: how many products to show after scoring
- `scraper.mode`: `playwright` | `cdp` | `camoufox`
- `scraper.proxy`: Russian proxy URL (with optional `user:pass@` for auth tunnel)
- `scraper.cdp_endpoint`: Chrome DevTools Protocol endpoint (default `http://127.0.0.1:9222`)
- `supplier1688`: enrichment config (enabled, mapping file, matching strategy, default values)
- `estimator`: estimation model params (commission rates, conversion weights, confidence rules)
- `ai`: DeepSeek API config for the AI analysis feature

## Testing

- 34 tests: unit tests for parser, merge, 1688 enrichment, estimator, and E2E browser tests
- `conftest.py` provides HTML fixture strings (realistic Ozon 2026 DOM structure) and a session-scoped Playwright browser
- E2E tests (`test_e2e.py`, `test_acceptance.py`) require a running Flask server — they auto-seed data via demo scrape if the server has zero products
- `sample_config` fixture provides a minimal config dict for tests that don't need the full YAML
