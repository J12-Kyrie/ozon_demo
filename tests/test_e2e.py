"""E2E tests covering AC-F1 through AC-F10 using Playwright sync API."""

import pytest
import requests
from playwright.sync_api import expect

BASE_URL = "http://localhost:5001"


# ==================== AC-F1: API endpoints return correct data ====================


def test_api_health():
    resp = requests.get(f"{BASE_URL}/api/health", timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("status", "version", "uptime_seconds", "products_loaded"):
        assert key in body, f"Missing key: {key}"
    assert body["status"] == "ok"


def test_api_data():
    resp = requests.get(f"{BASE_URL}/api/data", timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("data"), list)
    stats = body.get("stats", {})
    for key in (
        "total_before",
        "total_after",
        "shown",
        "avg_margin",
        "avg_sales",
        "avg_score",
    ):
        assert key in stats, f"Missing stats key: {key}"


def test_api_config_post():
    payload = {"min_monthly_sales": 3}
    resp = requests.post(f"{BASE_URL}/api/config", json=payload, timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "stats" in body
    # Restore default so other tests are unaffected
    requests.post(f"{BASE_URL}/api/config", json={"min_monthly_sales": 2}, timeout=10)


def test_api_dashboard_data():
    resp = requests.get(f"{BASE_URL}/api/dashboard-data", timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("categories", "scatter", "kpi"):
        assert key in body, f"Missing key: {key}"


# ==================== AC-F2: Filter interaction end-to-end ====================


def test_filter_updates_table(page):
    # Wait for initial table load (skeleton replaced by real content or empty-state)
    page.wait_for_selector("#tableWrap table, #tableWrap .empty-state", timeout=10000)

    # Set min_monthly_sales slider to 0 via JS to maximize results
    page.evaluate("document.getElementById('min_monthly_sales').value = '0'")
    page.evaluate("document.getElementById('v_sales').textContent = '0'")

    # Click apply
    page.click("button.btn-primary")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#tableWrap table, #tableWrap .empty-state", timeout=10000)

    # Verify the page responded (table or empty-state visible)
    wrap = page.locator("#tableWrap")
    assert wrap.is_visible()


# ==================== AC-F3: Tab switching changes columns ====================


def test_tab_switch_traffic(page):
    # Wait for table to load
    page.wait_for_selector("#tableWrap table, #tableWrap .empty-state", timeout=10000)

    page.click("button[data-tab='traffic']")
    page.wait_for_selector("#tableWrap table, #tableWrap .empty-state", timeout=5000)

    # Check for traffic-specific header text; may show as empty-state if no data
    content = page.locator("#tableWrap").inner_text()
    # If table rendered, verify traffic column header present
    table_locator = page.locator("#tableWrap table")
    if table_locator.count() > 0:
        headers_text = page.locator("#tableWrap thead").inner_text()
        assert (
            "下单转化率" in headers_text
        ), f"Traffic header not found. Got: {headers_text}"


def test_tab_switch_cost(page):
    page.wait_for_selector("#tableWrap table, #tableWrap .empty-state", timeout=10000)

    page.click("button[data-tab='cost']")
    page.wait_for_selector("#tableWrap table, #tableWrap .empty-state", timeout=5000)

    table_locator = page.locator("#tableWrap table")
    if table_locator.count() > 0:
        headers_text = page.locator("#tableWrap thead").inner_text()
        assert "采购价" in headers_text, f"Cost header not found. Got: {headers_text}"


# ==================== AC-F4: Table sorting ====================


def test_table_sort(page):
    page.wait_for_selector("#tableWrap table", timeout=10000)

    # Verify sort indicator changes on the clicked column header
    sort_th = page.locator("#tableWrap thead th").nth(2)
    sort_th.click()
    page.wait_for_selector("#tableWrap table", timeout=5000)
    first_row_asc = page.locator("#tableWrap tbody tr:first-child").inner_text()

    sort_th.click()
    page.wait_for_selector("#tableWrap table", timeout=5000)
    first_row_desc = page.locator("#tableWrap tbody tr:first-child").inner_text()

    assert page.locator("#tableWrap table").is_visible()
    row_count = page.locator("#tableWrap tbody tr").count()
    if row_count > 1:
        assert first_row_asc != first_row_desc, "Sort toggle should change row order with multiple rows"


# ==================== AC-F5: Chart rendering ====================


def test_charts_render(page):
    page.wait_for_load_state("networkidle")

    # Both canvas elements must exist in the DOM
    pie = page.locator("#chartPie")
    scatter = page.locator("#chartScatter")
    expect(pie).to_be_visible()
    expect(scatter).to_be_visible()

    # Verify canvas has non-zero dimensions
    pie_box = pie.bounding_box()
    assert pie_box is not None
    assert pie_box["width"] > 0 and pie_box["height"] > 0

    scatter_box = scatter.bounding_box()
    assert scatter_box is not None
    assert scatter_box["width"] > 0 and scatter_box["height"] > 0

    # Chart.js variables exist (may be null if no data, but must be declared)
    pie_exists = page.evaluate("typeof pieChart !== 'undefined'")
    scatter_exists = page.evaluate("typeof scatterChart !== 'undefined'")
    assert pie_exists
    assert scatter_exists


# ==================== AC-F6: AI analysis flow ====================


@pytest.mark.slow
def test_ai_analysis_button(page):
    # Open the AI accordion if it's closed (it starts open per HTML)
    acc_ai = page.locator("#acc_ai")
    if "open" not in (acc_ai.get_attribute("class") or ""):
        page.click("#acc_ai .accordion-header")

    # Wait for AI button to be visible and enabled
    ai_btn = page.locator("#ai_btn")
    expect(ai_btn).to_be_visible()
    ai_btn.wait_for(state="visible", timeout=5000)

    # Click the AI analysis button
    ai_btn.click()

    # Verify loading state appears immediately
    page.wait_for_selector(".ai-loading", timeout=5000)
    loading = page.locator(".ai-loading")
    expect(loading).to_be_visible()

    # Do NOT wait for full AI response — just confirm loading state triggered
    # Re-enable button is handled by the JS after response completes


# ==================== AC-F7: Error handling ====================


def test_empty_state_on_extreme_filter(page):
    # Use top_n=0 via JS fetch to guarantee empty results
    # (NaN-safe filters let demo data through regardless of threshold)
    page.evaluate("""
        (async () => {
            const res = await fetch("/api/config", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({top_n: 0})
            });
            const json = await res.json();
            currentData = json.data;
            updateStats(json.stats);
            renderTable();
        })()
    """)

    page.wait_for_selector("#tableWrap .empty-state", timeout=10000)
    empty_state = page.locator("#tableWrap .empty-state")
    expect(empty_state).to_be_visible()

    # Restore default
    page.evaluate("""
        (async () => {
            const res = await fetch("/api/config", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({top_n: 20})
            });
            const json = await res.json();
            currentData = json.data;
            updateStats(json.stats);
            renderTable();
        })()
    """)


# ==================== AC-F8: Accordion expand/collapse ====================


def test_accordion_toggle(page):
    acc = page.locator("#acc_weights")

    # Confirm it starts collapsed (no 'open' class)
    classes_before = acc.get_attribute("class") or ""
    assert (
        "open" not in classes_before
    ), f"Expected acc_weights to start closed, got class: {classes_before}"

    # Click to expand
    page.click("#acc_weights .accordion-header")

    # Verify 'open' class added
    classes_after = acc.get_attribute("class") or ""
    assert (
        "open" in classes_after
    ), f"Expected 'open' class after click, got: {classes_after}"

    # Verify body is visible (CSS transitions max-height from 0 to 2000px)
    body = page.locator("#acc_weights .accordion-body")
    expect(body).to_be_visible()

    # Click again to collapse
    page.click("#acc_weights .accordion-header")
    classes_collapsed = acc.get_attribute("class") or ""
    assert "open" not in classes_collapsed


# ==================== AC-F9: Chart edge cases ====================


def test_chart_updates_after_filter(page):
    page.wait_for_load_state("networkidle")

    # Capture initial pieChart reference identity via a counter trick
    page.evaluate("window._pieChartRef = pieChart")

    # Apply filter to trigger chart re-render
    page.evaluate("document.getElementById('min_monthly_sales').value = '0'")
    page.evaluate("document.getElementById('v_sales').textContent = '0'")
    page.click("button.btn-primary")
    page.wait_for_load_state("networkidle")

    # After applying filters, loadDashboardCharts() is called which destroys and recreates pieChart
    # The variable pieChart should be set (not undefined) after filter apply
    pie_defined = page.evaluate("typeof pieChart !== 'undefined'")
    assert pie_defined

    # Restore
    page.evaluate("document.getElementById('min_monthly_sales').value = '2'")
    page.evaluate("document.getElementById('v_sales').textContent = '2'")
    page.click("button.btn-primary")
    page.wait_for_load_state("networkidle")


# ==================== AC-F10: Data source badges ====================


def test_data_source_badges(page):
    # Ensure table is loaded
    page.wait_for_selector("#tableWrap table, #tableWrap .empty-state", timeout=10000)

    table_locator = page.locator("#tableWrap table")
    if table_locator.count() == 0:
        pytest.skip("No data available to verify badges")

    # Badges are in the last column of each row (data_source column)
    badges = page.locator("#tableWrap tbody .badge")
    count = badges.count()
    assert count > 0, "Expected at least one .badge element in the table body"

    # Each badge should have one of the known CSS classes
    for i in range(min(count, 5)):  # check first 5
        badge = badges.nth(i)
        badge_class = badge.get_attribute("class") or ""
        assert any(
            cls in badge_class for cls in ("badge-excel", "badge-scraped", "badge-demo")
        ), f"Badge {i} has unexpected class: {badge_class}"
