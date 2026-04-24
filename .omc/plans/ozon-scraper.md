# Ozon Real-Time Scraper — Implementation Plan

**Created:** 2026-04-15
**Status:** DRAFT — awaiting user confirmation
**Scope:** MVP scraper + GUI integration + tests
**Estimated Complexity:** MEDIUM

---

## RALPLAN-DR Summary

### Principles (5)

1. **Minimal disruption** — Extend the existing Flask app rather than rewriting; keep `RAW_DF` pattern intact.
2. **Scrape responsibly** — All timing/throttle parameters externalized to `config.yaml`; never hardcode delays.
3. **User visibility** — Scrape progress must be observable in the GUI in real time (not fire-and-forget).
4. **Testable parsing** — Separate page-fetching (Playwright) from data-extraction (pure functions on HTML) so parsing logic is unit-testable without a browser.
5. **Fail gracefully** — Scrape errors surface in the GUI with actionable messages; never crash the server.

### Decision Drivers (top 3)

| # | Driver | Weight |
|---|--------|--------|
| 1 | Anti-bot resilience — Ozon actively blocks scrapers; headless browser is mandatory | HIGH |
| 2 | MVP speed — Deliver working scraper fast; no proxy rotation, no auth, no DB | HIGH |
| 3 | Merge correctness — Scraped data must coexist with Excel data without duplicates or schema drift | MEDIUM |

### Viable Options

#### Option A: Background Thread + SSE Progress (RECOMMENDED)

Run Playwright scrape in a Python `threading.Thread`, push progress to the frontend via Server-Sent Events (SSE).

| Pros | Cons |
|------|------|
| No new dependencies beyond Playwright | Thread-safety requires a lock around shared DataFrame |
| SSE is natively supported by Flask (streaming response) | Only one scrape at a time (acceptable for MVP) |
| Simple mental model: click button, see progress, get results | SSE reconnection needs minimal client-side handling |

#### Option B: Celery + Redis Task Queue

Offload scrape to a Celery worker; poll task status from frontend.

| Pros | Cons |
|------|------|
| Battle-tested async pattern | Requires Redis + Celery — two extra services to install and run |
| Scales to multiple concurrent scrapes | Massive overkill for a single-user MVP |
| Clean task management | Significantly more complex deployment |

**Decision:** Option A. Option B is invalidated because the project is a single-user MVP demo with no deployment infrastructure. Adding Redis + Celery triples operational complexity for zero user benefit at this scale. Option B becomes viable only when multiple users need concurrent scrapes.

### ADR

- **Decision:** Background thread + SSE for scrape orchestration
- **Drivers:** MVP speed, single-user scope, minimal dependency footprint
- **Alternatives considered:** Celery + Redis (rejected: operational overhead disproportionate to MVP scope)
- **Why chosen:** Delivers real-time progress with zero new infrastructure; Playwright is the only new heavyweight dependency
- **Consequences:** Limited to one concurrent scrape; no persistent task history; thread lock adds minor code complexity
- **Follow-ups:** If multi-user support is added later, migrate to task queue; consider adding proxy rotation for production use

---

## Context

The existing app loads 100 Ozon products from a local `.xls` file into a pandas DataFrame at startup. All filtering and scoring happens in-memory. The goal is to add a real-time scraping capability that fetches fresh product data from Ozon category/search pages using Playwright, merges it with the local data, and exposes scrape controls in the existing GUI.

### Current Architecture

```
config.yaml --> main.py (Flask) --> templates/index.html
                   |
                   v
              RAW_DF (pandas, loaded from .xls at startup)
```

### Target Architecture

```
config.yaml --> main.py (Flask) --> templates/index.html
                   |                      |
                   v                      v
              RAW_DF (pandas)      Scrape UI section
                   ^                      |
                   |                      v
              merge <---------- scraper.py (Playwright)
                                     |
                                     v
                                parser.py (HTML -> dict)
```

---

## Work Objectives

1. Create a scraper module that navigates Ozon pages with Playwright, extracts product data, and returns structured records
2. Add GUI controls for triggering scrapes and viewing progress
3. Merge scraped results into the existing in-memory DataFrame with deduplication
4. Provide test coverage for the parsing/extraction layer
5. Externalize all scrape-related configuration to `config.yaml`

---

## Guardrails

### Must Have
- Playwright for all page interaction (mandatory — Ozon anti-bot)
- All scrape timing parameters in `config.yaml`
- Unit tests for HTML parsing logic (no browser required for tests)
- Progress feedback in the GUI during scrape
- Deduplication by SkuId when merging scraped + Excel data

### Must NOT Have
- No proxy rotation (out of MVP scope)
- No user authentication or login to Ozon
- No database / persistent storage of scraped data (in-memory only, like existing app)
- No Celery/Redis or external task queue
- No modification to the existing filter/score logic (only the data source changes)

---

## Config Additions (exact `config.yaml` additions)

```yaml
scraper:
  delay_between_pages: 3.0      # seconds between page navigations
  delay_between_requests: 1.5   # seconds between individual requests within a page
  max_pages: 5                  # maximum number of pages/scrolls to scrape per run
  timeout: 30                   # seconds — Playwright page timeout
  headless: true                # run browser in headless mode
  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
```

---

## Task Flow (5 phases)

### Phase 1: Scraper Core (`scraper.py` + `parser.py`)

**Files created:**
- `scraper.py` — Playwright browser management, page navigation, scroll/pagination handling
- `parser.py` — Pure functions that extract product data from HTML content (no browser dependency)

**`scraper.py` key functions:**
- `async def scrape_category(url: str, config: dict, progress_callback) -> list[dict]` — Navigate to category/search URL, scroll through pages, collect raw HTML per product card, call parser, return list of product dicts
- `async def scrape_product_detail(url: str, config: dict) -> dict` — (Optional, stretch) Visit individual product page for full details
- `def run_scrape(url: str, config: dict, progress_callback) -> list[dict]` — Synchronous wrapper that creates an asyncio event loop and runs `scrape_category`

**`parser.py` key functions:**
- `def parse_product_card(html: str) -> dict` — Extract title, price, rating, review_count, seller, image_url, sku_id from a single product card's HTML
- `def parse_search_page(page_html: str) -> list[str]` — Extract individual product card HTML snippets from a full page
- `def normalize_product(raw: dict) -> dict` — Map scraped field names to the column names used by the existing app (e.g., map scraped `price` to the column schema expected by `filter_and_score`)

**Acceptance criteria:**
- [ ] `scraper.py` can open an Ozon category URL in headless Playwright, scroll through up to `max_pages` pages, and return a list of product dicts
- [ ] `parser.py` functions are pure (no browser, no network) and operate on HTML strings
- [ ] Delay between pages respects `config.scraper.delay_between_pages`
- [ ] Progress callback is invoked with `{"current_page": N, "total_pages": M, "products_found": K}` after each page
- [ ] Errors during scraping are caught and returned as structured error dicts, not raised exceptions

### Phase 2: Flask Integration (modify `main.py`)

**File modified:** `main.py`

**Changes:**
- Add `import threading` and import from `scraper.py`
- Add module-level scrape state: `SCRAPE_STATUS = {"running": False, "progress": {}, "error": None}`
- Add thread lock: `SCRAPE_LOCK = threading.Lock()`
- Add route `POST /api/scrape` — Accepts `{"url": "...", "keyword": "..."}`, validates input, starts scrape in background thread, returns `{"status": "started"}`
- Add route `GET /api/scrape/status` — Returns current `SCRAPE_STATUS` as JSON (for polling fallback)
- Add route `GET /api/scrape/stream` — SSE endpoint that yields `SCRAPE_STATUS` updates every second until scrape completes
- Add merge function: `def merge_scraped(new_records: list[dict])` — Converts scraped records to DataFrame, deduplicates by SkuId against `RAW_DF`, appends new rows, updates global `RAW_DF`
- Scrape thread function: runs `run_scrape()`, calls `merge_scraped()` on success, updates `SCRAPE_STATUS` throughout

**Acceptance criteria:**
- [ ] `POST /api/scrape` rejects requests when a scrape is already running (returns 409)
- [ ] `POST /api/scrape` accepts either `url` or `keyword` (keyword gets converted to `https://www.ozon.ru/search/?text={keyword}`)
- [ ] SSE stream sends JSON events with `current_page`, `total_pages`, `products_found`, `status` fields
- [ ] After scrape completes, `RAW_DF` contains merged data with no duplicate SkuIds
- [ ] Existing `/api/data` and `/api/config` endpoints work unchanged with the expanded DataFrame
- [ ] If scrape fails, error message is stored in `SCRAPE_STATUS` and surfaced to client

### Phase 3: GUI Scrape Section (modify `templates/index.html`)

**File modified:** `templates/index.html`

**Changes — Sidebar addition (above the "Apply" button):**
- New section title: "Data Scraping" (数据采集)
- Text input for Ozon URL or keyword
- "Start Scrape" button (disabled while scrape is running)
- Progress indicator: text showing "Page X/Y — Z products found" updated via SSE
- Status badge: idle / running / completed / error
- After completion, auto-refresh the data table with merged results

**Changes — Stats bar:**
- Add a small indicator showing "Excel: N | Scraped: M | Total: N+M" so users can see data source breakdown

**Acceptance criteria:**
- [ ] User can paste an Ozon category URL and click "Start Scrape"
- [ ] User can type a keyword and click "Start Scrape" (URL is constructed automatically)
- [ ] Progress updates appear in real time without page refresh
- [ ] Button is disabled and shows spinner/loading state during scrape
- [ ] On completion, the product table auto-refreshes with merged data
- [ ] On error, a clear error message appears in the scrape section
- [ ] All new UI elements follow the existing dark theme (use CSS variables already defined)

### Phase 4: Tests (`tests/`)

**Files created:**
- `tests/__init__.py` — empty
- `tests/test_parser.py` — Unit tests for `parser.py`
- `tests/conftest.py` — Shared fixtures (sample HTML snippets)
- `tests/test_scraper_integration.py` — Integration test for scraper (mocked Playwright)

**`tests/test_parser.py` test cases:**
- `test_parse_product_card_valid` — Feed a realistic Ozon product card HTML, assert all fields extracted correctly
- `test_parse_product_card_missing_fields` — HTML with missing price/rating, assert graceful defaults (None or 0)
- `test_parse_search_page_multiple_cards` — Full page HTML with 3+ cards, assert correct count extracted
- `test_parse_search_page_empty` — Page with no product cards returns empty list
- `test_normalize_product_maps_fields` — Raw scraped dict maps to expected column names
- `test_normalize_product_handles_missing` — Missing fields in raw dict produce None in normalized output

**`tests/test_scraper_integration.py` test cases:**
- `test_run_scrape_calls_progress` — Mock Playwright page, verify progress callback is called with correct structure
- `test_run_scrape_respects_max_pages` — Set `max_pages: 2`, verify only 2 page navigations occur
- `test_merge_deduplicates` — Create RAW_DF with known SkuIds, merge overlapping scraped records, assert no duplicates

**`tests/conftest.py` fixtures:**
- `sample_product_card_html` — Realistic HTML snippet of one Ozon product card
- `sample_search_page_html` — Full page HTML with multiple product cards
- `sample_config` — Config dict with scraper section populated

**Acceptance criteria:**
- [ ] `pytest tests/test_parser.py` passes with all 6 test cases green
- [ ] `pytest tests/test_scraper_integration.py` passes with all 3 test cases green (no real browser launched)
- [ ] Parser tests run in under 1 second (no I/O)
- [ ] Tests do not depend on network access or a running Flask server

### Phase 5: Config + Dependencies + Documentation

**Files created/modified:**
- `config.yaml` — Add `scraper:` section (see Config Additions above)
- `requirements.txt` — Create with all dependencies:
  ```
  flask
  pandas
  numpy
  pyyaml
  openpyxl
  xlrd
  playwright
  pytest
  ```

**Post-install step (documented, not automated):**
- `playwright install chromium` — Must be run once after `pip install playwright`

**Acceptance criteria:**
- [ ] `pip install -r requirements.txt && playwright install chromium` sets up a working environment
- [ ] `config.yaml` loads without error with the new `scraper` section
- [ ] Existing app behavior is unchanged when scraper section is present but no scrape is triggered
- [ ] `load_config()` in `main.py` returns the scraper config without error

---

## Success Criteria (overall)

1. User can open the web GUI, paste an Ozon category URL or keyword, click "Start Scrape", and see progress updates in real time
2. After scrape completes, the product table updates with merged data (Excel + scraped), deduplicated by SkuId
3. All scrape timing parameters are configurable via `config.yaml` — changing `delay_between_pages` from 3 to 5 takes effect on next scrape without code changes
4. `pytest` runs 9 tests, all passing, in under 5 seconds with no network or browser dependency
5. The existing filter/score/display workflow is completely unaffected when no scrape is triggered

---

## File Summary

| File | Action | Description |
|------|--------|-------------|
| `scraper.py` | CREATE | Playwright browser management, page navigation, scrape orchestration |
| `parser.py` | CREATE | Pure HTML parsing functions, field extraction, normalization |
| `main.py` | MODIFY | Add scrape endpoints, background thread, merge logic, SSE stream |
| `templates/index.html` | MODIFY | Add scrape UI section, progress display, SSE client |
| `config.yaml` | MODIFY | Add `scraper:` configuration section |
| `requirements.txt` | CREATE | Pin all project dependencies |
| `tests/__init__.py` | CREATE | Empty package marker |
| `tests/conftest.py` | CREATE | Shared test fixtures (sample HTML) |
| `tests/test_parser.py` | CREATE | 6 unit tests for parser functions |
| `tests/test_scraper_integration.py` | CREATE | 3 integration tests with mocked Playwright |

**Total: 4 files created, 3 files modified, 3 test files created**

---

## Open Questions

See `.omc/plans/open-questions.md` for tracked items.
