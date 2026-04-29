# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Identity

Ozon 选品助手 — an MVP web app for cross-border e-commerce sellers. Scrapes Ozon (Russian e-commerce) product listings, enriches them with 1688 supplier data and rule-based mathematical estimates, then scores/filters products. Flask + Playwright CDP + pandas.

## Commands

```bash
source .venv/bin/activate          # Virtual environment (Python 3.12)
python main.py                      # Flask dev server (port 5001)
pytest tests/ -v                    # All unit tests (32 tests)
pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/test_acceptance.py -v
ruff format . && ruff check .       # Format + lint
python scripts/benchmark_parser.py  # Parser accuracy benchmark (last line = JSON)
python scripts/collect_fixtures.py --mode cdp --count 2  # Collect Ozon HTML fixtures
python scripts/scrape_1688_tab.py --interactive           # 1688 product scraper
docker compose up --build -d && docker compose down       # Docker deployment
```

## Architecture: Five-Stage Data Pipeline

```
scrape (CDP/Playwright) → parse (regex HTML) → 1688 enrich (CSV + live) → estimate (math) → score (weighted ranking)
                                              ↓
                                        main.py orchestrates all stages + Flask routes
```

### Stage 1: Scrape (`scraper.py`, 556 lines)

Three modes configured via `config.yaml` → `scraper.mode`:
- **`cdp`** (primary): attaches to user's real Edge browser via Chrome DevTools Protocol. Inherits the browser's fingerprint, cookies, and Ozon login session to bypass anti-bot.
- **`playwright`**: headless Chromium with optional proxy tunnel.
- **`camoufox`**: anti-detection Firefox (requires `pip install camoufox`).

Data flow within a scrape:
1. `_extract_products()` — navigates to category/search URL → detects blocks (HTTP 403, "доступ ограничен", captcha) → paginates through `max_pages` (default 5) → collects product cards via `parse_search_page()`.
2. **Page-level JSON-LD extraction**: `_extract_page_categories()` parses `<script type="application/ld+json">` BreadcrumbList and `state-breadCrumbs` data-state attributes to extract category hierarchy (e.g. `Электроника > Телефоны и смарт-часы`). Categories are injected into every product card as `list_category_level1/level3`.
3. **Detail enrichment**: `enrich_products_with_detail()` visits TopK product detail pages (default `detail_top_k=150`, async semaphore with `detail_concurrency=3`). Each detail page is processed by `extract_detail_fields()` which uses Russian keyword regex:
   - `Бренд` → brand, `Продавец`/`Магазин` → seller name
   - `Вес товара` (г/кг) → weight, `Длина×Ширина×Высота` (mm/cm/см) → dimensions
   - `другие продавцы`/`предложения` → competitor count
   - `sellerId`/`seller_id` → seller ID
4. Demo mode: `run_demo_scrape()` generates 15 synthetic products, no network.

### Stage 2: Parse (`parser.py`, 340 lines)

Pure regex HTML parsing — no browser dependency.

- `parse_search_page()`: finds Ozon `tile-root` divs via tag regex, extracts each card.
- `parse_product_card()`: extracts SKU (URL slug `-\d{5,}/` or `data-sku`), title (longest span candidate after skip-pattern filtering), current price (first `₽`), original price (only when `<s>`/`<del>`/`line-through`/`strikethrough` markers exist), rating (adjacent or standalone `X.X`), reviews (Russian `отзыв`/`отзыва`/`отзывов`), seller (`data-seller` attribute).
- `normalize_product()`: maps to 41-field Chinese column schema. Uses `first_non_null()` priority: detail page fields > list page fields > None. Zero is always valid (not missing).
- `_extract_rating_and_reviews()`: multi-stage extraction — `data-rating` attribute → `aria-label` → adjacent regex → standalone rating → standalone review count (Russian).

### Stage 3: 1688 Enrichment (`supplier_1688.py`, 280 lines)

Two-tier approach:
- **Tier 1 — CSV mapping**: `supplier_mapping.csv` (ozon_sku → supplier_keyword, purchase_price, freight_est). Deterministic, fast. 7 SKUs maintained manually.
- **Tier 2 — Live scraping**: `scrape_1688_product()` uses Playwright to search 1688 for a keyword and extract prices from the first result. Supports CDP mode for logged-in browser sessions. Detects login walls and captcha. Chinese regex: `¥/￥` prices, `运费`/`快递` freight, `起批`/`≥` MOQ.

`fetch_supplier_fields()`: CSV first → live scrape fallback (if `live_scrape_enabled=true`) → config defaults last.

`scripts/scrape_1688_tab.py`: interactive helper — waits for user to manually open a 1688 product page in the browser, then auto-scrapes price/freight/supplier data. Modes: `--interactive` (poll for detail tab), `--keyword` (auto-search).

### Stage 4: Estimate (`estimator.py`, 270 lines)

Rule-based mathematical models. All outputs use `_est` suffix — **never overwrite real columns**. Currently 14 output columns.

**Category-aware rate tables** (10 categories, Russian names):
- Commission: `CATEGORY_COMMISSION_RATES` (Электроника=10%, Одежда=15%, etc.)
- Sales ratio: `REVIEW_SALES_RATIO` (电子产品=20, 服装=5, etc.)

**Estimation chain** (卢布核算, CNY→RUB via configurable `cny_to_rub_rate`):
```
采购成本 = 1688阶梯价 × fx
计费重 = max(实重_kg, 长×宽×高/5000)
Ozon物流费 = tariff_base + weight_rate × ceil(计费重)
平台佣金 = 绿标价 × commission_rate[一级类目]
总成本 = 采购 + 运费 + 物流 + 佣金 + 绿标价×(广告率+损耗率)
利润 = 绿标价 − 总成本
毛利率 = (绿标价 − 总成本) / 绿标价 × 100%
月销量_est = 评论数 × review_sales_ratio[类目] × rating_factor × competition_factor
转化率_est = clip(base_cvr × exp(...)), 4-维度加权
```

### Stage 5: Score & Filter (`main.py`, 680 lines)

`filter_and_score()` pipeline:
1. Apply configurable filters (min sales ≥2, min margin ≥30%, max competitors ≤35, max purchase ≤500¥, min price ≥50₽)
2. `_safe_filter()`: NaN values **pass** (missing data ≠ failed check)
3. `_with_est_fallback()`: `combine_first()` — real column preferred, `_est` fallback when null
4. Filter to `data_source == "scraped"` only (Excel rows excluded from ranking)
5. 4-dimension normalization [0,1] → weighted sum → `综合评分` (0–100)
6. Top N output (config: `output.top_n`)

`merge_dataframes()`: Excel rows win for existing values. Null cells in Excel rows are backfilled from scraped non-null values. New scraped-only SKUs are appended.

Scrape runs in a **background thread** (`_run_scrape_thread()`), progress reported via shared `SCRAPE_STATUS` dict + SSE stream (`/api/scrape/stream`).

**Data persistence**: `find_excel()` prefers `scraped_merged.xlsx` (falls back to newest `.xls`/`.xlsx`). `load_data()` preserves existing `data_source` column. `persist_merged_data()` writes after every scrape.

## Input/Output Column Groups

**41 output fields** per product. Frontend tabs backed by:

- `TAB_BASIC_COLS` (12): SkuId, 评级, 品牌, 店铺名称, 一级/三级类目, 绿标价, 黑标价, 月销量, 月销售额, 商品链接, + 评论数, 标题
- `TAB_TRAFFIC_COLS` (10): SkuId + conversion/CTR/cart/impression/views + `_est` fallbacks
- `TAB_COST_COLS` (22): SkuId + 1688 purchase/freight, Ozon logistics/commission, margin/profit/cost + all `_est` variants
- `EST_DATA_COLS` (14): separated `_est` columns for API output visibility

**6-field parser benchmark** (sealed: `scripts/benchmark_parser.py`): sku_id, title, price_current, price_original, rating, seller — measured against 232 real Ozon cards in `tests/fixtures/`.

## Key Design Decisions

- **No database**: pandas DataFrames → `scraped_merged.xlsx` for persistence.
- **CDP for anti-bot**: attaches to user's real browser, inherits fingerprint + session. Headless Playwright gets HTTP 403 on Ozon.
- **Scraped-only scoring**: `filter_and_score()` filters to `data_source == "scraped"` → Excel rows are reference data, not ranked.
- **Real-value priority, estimate fallback**: `_est` columns never overwrite real columns. `_with_est_fallback()` uses `combine_first()`.
- **Page-level data injection**: JSON-LD breadcrumbs and state-breadCrumbs are extracted once per page and injected into all scraped cards as `list_category_*`.
- **Russian keyword regex**: all detail page extraction uses Russian text: `Бренд`, `Продавец`, `Вес товара`, `отзыв`, `другие продавцы`.
- **1688 conservative**: CSV mapping is primary; live scraping is opt-in (`live_scrape_enabled: false`) due to captcha/login requirements.
- **Config-driven**: all thresholds, weights, rates, and modes in `config.yaml`.

## Real Fixtures & Benchmark

`tests/fixtures/`: 6 real Ozon category pages (232 product cards, 3 categories: smartphones, electronics, laptops). `fixture_N.html` + `fixture_N_truth.json` (Playwright DOM-extracted ground truth). Benchmark score: **84.8%** on 6 key fields (target: 90%).

`scripts/collect_fixtures.py`: collects new fixtures via CDP/Playwright. `DOM_EXTRACT_JS` uses class-based price disambiguation, element-level rating detection, text-based seller fallback.

## Russian & Chinese Keyword Reference

| Purpose | Russian | Chinese |
|---------|---------|---------|
| Price | `₽`, `скидк` (discount) | `¥`, `￥` |
| Reviews | `отзыв`, `отзыва`, `отзывов` | `评价`, `评论` |
| Rating | `рейтинг`, `звёзд` | — |
| Seller | `продавец`, `магазин` | `供应商`, `公司` |
| Brand | `бренд` | — |
| Weight | `вес товара`, `вес с упаковкой` | — |
| Dimensions | `длина`, `ширина`, `высота` | — |
| Delivery | `доставка` | `运费`, `快递` |
| Stock | `шт осталось`, `наличи` | `库存` |
| Competitors | `другие продавцы`, `предложения` | — |
| Freight | — | `运费`, `物流` |
| MOQ | — | `起批`, `起订`, `≥` |

## Testing

32 unit tests: parser (14) + merge (4) + 1688 (3) + upgrade/estimator/dashboard (11).
`conftest.py`: HTML fixtures + session-scoped Playwright browser.
E2E tests (`test_e2e.py`, `test_acceptance.py`): require Flask running, auto-seed via demo scrape.
`sample_config` fixture for tests that don't need full YAML.
