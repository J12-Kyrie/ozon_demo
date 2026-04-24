# Implementation Plan: Ozon Demo Product Upgrade

**Spec:** `.omc/specs/deep-interview-ozon-product-upgrade.md`
**Date:** 2026-04-21
**Ambiguity:** 5.1% (PASSED, 18-round interview)
**Complexity:** HIGH (4 parallel feature tracks, major UI rewrite)

---

## RALPLAN-DR Summary

### Principles (5)

1. **Brownfield safety** -- Never break existing scraper flow (scraper.py, parser.py untouched). All new features are additive.
2. **Single-page Flask** -- No frontend framework. Pure HTML/CSS/JS with CDN libs (Chart.js, marked.js). Keep the monolithic template pattern.
3. **Cost-controlled AI** -- DeepSeek API via OpenAI SDK, batch mode on TOP N filtered products (~20), deepseek-chat model, one-shot return (not streaming).
4. **Demo-ready polish** -- Google color scheme continuity, smooth CSS transitions, accordion sidebar, tab-grouped table. Must look "product-grade" in a live demo.
5. **Minimal new files** -- Prefer inline additions to main.py over new modules. Only create `ai_analyzer.py` if the AI logic exceeds ~60 lines (it will).

### Decision Drivers (top 3)

1. **User wants all 4 tracks in parallel** -- Dashboard, expanded table, AI analysis, and UI polish are all mandatory. Plan must sequence them so each step produces a working intermediate state.
2. **Template is the bottleneck** -- `index.html` (~506 lines) needs the heaviest rewrite. All 4 tracks converge on this file. Steps must be ordered to avoid merge conflicts if parallelized.
3. **AI integration is the highest-risk item** -- External API dependency, prompt engineering, error handling, and hover-linking all live here. Isolate this into a separate module + route to contain blast radius.

### Viable Options

#### Option A: Incremental In-Place (CHOSEN)

Modify existing files incrementally. Add `ai_analyzer.py` as the only new Python file. Rewrite `index.html` in-place with the new layout.

| Pros | Cons |
|------|------|
| Minimal file count change (4 modified, 1 new) | Template rewrite is large (~800+ lines estimated) |
| Matches existing patterns | Cannot easily parallelize template work |
| No build tooling needed | Single HTML file gets complex |
| Easy to test incrementally | |

#### Option B: Split Template into Partials (Jinja2 includes)

Break `index.html` into `base.html`, `_sidebar.html`, `_dashboard.html`, `_table.html` partials.

| Pros | Cons |
|------|------|
| Cleaner separation of concerns | Adds 4-5 new template files |
| Easier to parallelize development | Over-engineering for a demo app |
| Each partial is independently testable | Flask template inheritance adds complexity |
| | Diverges from existing single-file pattern |

**Why Option B was not chosen:** The spec explicitly says "keep Flask single-page application." The current codebase uses one template file. Introducing partials adds structural complexity disproportionate to a demo app. The user's "demo-ready" criterion values visual polish over code architecture. Option A preserves the existing pattern while delivering all required features.

### ADR

- **Decision:** Incremental in-place modification (Option A)
- **Drivers:** Demo-ready focus, minimal structural change, single-page Flask constraint
- **Alternatives considered:** Template partials (Option B)
- **Why chosen:** Matches existing pattern, avoids over-engineering, user explicitly chose "simple and direct" approach during interview (Round 11)
- **Consequences:** Template file will be large (~900 lines). Acceptable for a demo. If project grows beyond demo, should revisit partials.
- **Follow-ups:** Add `.gitignore` entry for config.yaml (contains API key). Consider template partials if a second developer joins.

---

## Context

### Current State
- **main.py** (291 lines): Flask app with routes `/`, `/api/data`, `/api/config`, `/api/scrape`, `/api/scrape/status`, `/api/scrape/stream`. Loads Excel data, filters/scores products, serves JSON.
- **templates/index.html** (506 lines): Single-page UI with sidebar (filters + weights + scrape controls) and main area (stats bar + sortable table). Pure CSS with Google color variables. JS handles sorting, filtering, SSE scrape progress.
- **config.yaml** (30 lines): Filters, scoring weights, output config, scraper config. No AI section.
- **KEY_COLS** in main.py: 12 columns currently displayed. Spec requires ~30 columns across 3 tab groups.
- **requirements.txt**: flask, pandas, numpy, pyyaml, openpyxl, xlrd, playwright, pytest, pytest-asyncio. Missing: `openai`.

### What Changes
| File | Action | Scope |
|------|--------|-------|
| `config.yaml` | Modify | Add `ai` block (api_key, base_url, model, max_products, prompt_template) |
| `requirements.txt` | Modify | Add `openai` |
| `ai_analyzer.py` | **New** | DeepSeek API integration module (~80 lines) |
| `main.py` | Modify | Expand KEY_COLS to 3 tab groups, add `/api/ai-analyze` route, add `/api/dashboard-data` route |
| `templates/index.html` | Modify (major) | Dashboard section, tab-grouped table, accordion sidebar, AI report panel, Chart.js + marked.js CDN |

---

## Guardrails

### Must Have
- All 6 acceptance criteria (AC1-AC6) from spec
- Existing scraper functionality unbroken (scraper.py and parser.py untouched)
- Google color scheme preserved (existing CSS variables)
- API key read from config.yaml `ai.api_key`
- Error handling with retry button for AI calls

### Must NOT Have
- No frontend framework (React/Vue)
- No mobile-responsive design
- No per-product AI analysis popups
- No streaming AI response
- No changes to scraper.py or parser.py
- No user authentication

---

## Task Flow

```
Step 1 (config + dependency)
    |
Step 2 (ai_analyzer.py + backend routes)
    |
Step 3 (template rewrite: layout + sidebar + dashboard)
    |
Step 4 (template: tab table + AI panel + charts)
    |
Step 5 (integration + polish + verification)
```

Steps 1-2 are backend-only. Steps 3-4 are frontend-heavy. Step 5 ties everything together.

---

## Detailed Steps

### Step 1: Config and Dependencies

**Files:** `config.yaml`, `requirements.txt`

**Changes to `config.yaml`:**
Add an `ai` section at the end:
```yaml
ai:
  api_key: "sk-YOUR-DEEPSEEK-KEY"
  base_url: "https://api.deepseek.com"
  model: "deepseek-chat"
  max_products: 20
  prompt_template: |
    你是一位跨境电商选品专家。请基于以下 Ozon 平台商品数据，生成商业决策分析报告。

    ## 数据
    {data}

    ## 请按以下结构输出分析（Markdown格式）：
    ### TOP 推荐
    最值得跟卖的 3-5 个商品及具体理由（结合毛利率、销量、竞争度）

    ### 风险提示
    高竞争/低毛利/价格异常的商品预警

    ### 类目趋势
    各一级类目的销售表现总结

    ### 价格策略建议
    基于成本结构的定价优化建议
```

**Changes to `requirements.txt`:**
Add `openai` (DeepSeek is OpenAI-compatible).

**Acceptance Criteria:**
- [ ] `config.yaml` loads without error with `yaml.safe_load()`
- [ ] `openai` is listed in requirements.txt
- [ ] Existing config fields unchanged (filters, scoring, output, scraper)

---

### Step 2: AI Analyzer Module + Backend Routes

**New file: `ai_analyzer.py`** (~80 lines)

Responsibilities:
- `prepare_ai_payload(df, max_products)` -- Select top N products, extract ~10 core fields (SkuId, 一级类目, 三级类目, 绿标价, 月销量, 毛利率, 利润(预), 跟卖数量, 展示至下单转化率, 阿里巴巴采购价(预)), return JSON string
- `generate_report(config, payload)` -- Call DeepSeek API via OpenAI SDK, return Markdown string
- Error handling: catch `openai.APIError`, `openai.APIConnectionError`, `openai.RateLimitError`, return structured error dict

**Changes to `main.py`:**

1. Expand `KEY_COLS` into 3 tab group constants:
   - `TAB_BASIC_COLS` -- SkuId, 评级, 品牌, 店铺名称, 一级类目, 三级类目, 绿标价, 黑标价, 月销量, 月销售额, 综合评分, 商品链接
   - `TAB_TRAFFIC_COLS` -- SkuId, 展示至下单转化率, 商品卡加购率, 搜索至加购转化率, 详情页访问量, 曝光次数, 销售动态占比
   - `TAB_COST_COLS` -- SkuId, 阿里巴巴采购价(预), 1688运费, ozon物流费(预), 平台佣金(预), 佣金比例%, 总成本, 成本占比, 毛利率, 利润(预)

2. Modify `filter_and_score()`:
   - Return ALL columns from the 3 tab groups (union), not just KEY_COLS
   - Add additional stats: `avg_rating` (for dashboard KPI card), category distribution counts (for pie chart), scatter data points (for margin vs sales chart)

3. Add route `POST /api/ai-analyze`:
   - Load config, get filtered data
   - Call `ai_analyzer.prepare_ai_payload()` then `ai_analyzer.generate_report()`
   - Return `{"report": "<markdown>", "product_ids": [...]}` or `{"error": "message"}`

4. Add route `GET /api/dashboard-data`:
   - Return aggregated data for charts: `{"categories": {...}, "scatter": [...], "kpi": {...}}`
   - Categories: `{name: count}` for pie chart
   - Scatter: `[{sku, name, margin, sales}]` for scatter plot
   - KPI: total_products, filtered_count, avg_margin, avg_sales, avg_score

**Acceptance Criteria:**
- [ ] `POST /api/ai-analyze` returns valid JSON with `report` key (Markdown string) or `error` key
- [ ] `GET /api/dashboard-data` returns categories dict, scatter array, kpi dict
- [ ] `GET /api/data` returns all columns needed for 3 tabs (not just old KEY_COLS)
- [ ] AI call uses `deepseek-chat` model, sends ~10 fields per product, max 20 products
- [ ] API errors return `{"error": "friendly message"}` with HTTP 200 (frontend handles display)
- [ ] Existing routes (`/api/config`, `/api/scrape/*`) unchanged in behavior

---

### Step 3: Template Rewrite -- Layout, Sidebar Accordion, Dashboard

**File:** `templates/index.html`

This step restructures the page layout and adds the dashboard. The table and AI panel come in Step 4.

**Layout restructure:**
```
<body>
  <div class="sidebar">          (accordion groups)
  <div class="main">
    <div class="dashboard">       (NEW: KPI cards + charts)
    <div class="tab-bar">         (NEW: tab switching)
    <div class="table-wrap">      (existing, modified)
  </div>
</body>
```

**Sidebar accordion conversion:**
Convert existing flat sidebar into collapsible accordion groups:
- Group 1: "筛选条件" (default: EXPANDED) -- all filter sliders + category select
- Group 2: "AI 智能分析" (default: EXPANDED) -- analyze button, loading state, report output area
- Group 3: "评分权重" (default: COLLAPSED) -- weight sliders
- Group 4: "数据采集" (default: COLLAPSED) -- scrape input, buttons, progress

Each group: clickable header with chevron icon, smooth expand/collapse CSS transition.

**Dashboard KPI cards (above table):**
Upgrade existing `.stats-bar` to a proper dashboard section:
- Card 1: 原始商品数 (total)
- Card 2: 筛选通过数 (filtered) -- green accent
- Card 3: 平均毛利率 -- with % suffix
- Card 4: 平均月销量
- Card 5: 平均评分 -- blue accent

**Dashboard Charts (2x, below KPI cards):**
- Add `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>` to head
- Chart 1: Category distribution pie chart (`<canvas id="chartPie">`)
- Chart 2: Margin vs Sales scatter plot (`<canvas id="chartScatter">`)
- Charts container: CSS grid, 2 columns on wide screens
- Charts populated by calling `/api/dashboard-data` and rendering via Chart.js

**CSS additions:**
- Accordion styles: `.accordion-group`, `.accordion-header`, `.accordion-body`, `.accordion-chevron`
- Dashboard styles: `.dashboard-section`, `.charts-row`, `.chart-card`
- Transition animations: `max-height` transition for accordion, `opacity` transition for chart cards

**Acceptance Criteria:**
- [ ] Sidebar has 4 accordion groups, each clickable to expand/collapse
- [ ] Default state: filters + AI expanded, weights + scraping collapsed
- [ ] Dashboard shows 5 KPI cards with correct data from API
- [ ] Pie chart renders category distribution (Chart.js)
- [ ] Scatter chart renders margin vs sales (Chart.js)
- [ ] Charts update when filters change (re-fetch dashboard-data)
- [ ] Existing filter/weight/scrape controls still function identically
- [ ] Google color scheme preserved (all existing CSS variables intact)
- [ ] Smooth accordion open/close animation (CSS transition, no jank)

---

### Step 4: Template -- Tab-Grouped Table + AI Report Panel

**File:** `templates/index.html` (continued)

**Tab-grouped table:**
Add a tab bar above the table with 3 tabs:
- Tab "基础信息" (default active)
- Tab "流量转化"
- Tab "成本结构"

Each tab shows a different subset of columns (defined in JS `TAB_CONFIGS` matching the backend `TAB_*_COLS`). Tab switching is pure JS -- re-render table with different column set. No API call needed (all data already loaded from `/api/data`).

JS column definitions per tab:
```javascript
const TAB_CONFIGS = {
  basic: [
    {key:"综合评分", label:"评分", type:"score"},
    {key:"SkuId", label:"SKU ID", type:"sku"},
    {key:"评级", label:"评级", type:"num"},
    {key:"品牌", label:"品牌", type:"text"},
    {key:"店铺名称", label:"店铺", type:"text"},
    {key:"一级类目", label:"一级类目", type:"text"},
    {key:"三级类目", label:"三级类目", type:"text"},
    {key:"绿标价", label:"售价(R)", type:"num"},
    {key:"黑标价", label:"黑标价(R)", type:"num"},
    {key:"月销量", label:"月销量", type:"num"},
    {key:"月销售额", label:"月销售额", type:"num"},
    {key:"商品链接", label:"链接", type:"link"},
    {key:"data_source", label:"来源", type:"badge"},
  ],
  traffic: [
    {key:"SkuId", label:"SKU ID", type:"sku"},
    {key:"展示至下单转化率", label:"下单转化率%", type:"num"},
    {key:"商品卡加购率", label:"加购率%", type:"num"},
    {key:"搜索至加购转化率", label:"搜索加购%", type:"num"},
    {key:"详情页访问量", label:"详情页访问", type:"num"},
    {key:"曝光次数", label:"曝光次数", type:"num"},
    {key:"销售动态占比", label:"销售动态%", type:"num"},
    {key:"data_source", label:"来源", type:"badge"},
  ],
  cost: [
    {key:"SkuId", label:"SKU ID", type:"sku"},
    {key:"阿里巴巴采购价(预)", label:"采购价(Y)", type:"num"},
    // ... remaining cost columns
    {key:"毛利率", label:"毛利率%", type:"num"},
    {key:"利润(预)", label:"预估利润", type:"num"},
    {key:"data_source", label:"来源", type:"badge"},
  ],
};
```

**Table row identification:**
Each `<tr>` gets a `data-sku="..."` attribute for AI hover-linking.

**AI Report Panel (in sidebar, Group 2):**
- "开始 AI 分析" button (blue, Google accent color)
- Loading state: spinner animation + "DeepSeek 正在分析 N 件商品..." text
- Report area: `<div id="ai-report">` renders Markdown via marked.js
- Add `<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>` to head
- Report header badge: "DeepSeek AI" with distinct styling
- Error state: friendly message + "重试" button
- Hover linking: After report renders, scan for SkuId or product names in report text, wrap them in `<span class="ai-product-ref" data-sku="...">`. On mouseenter, find matching table row and add `.ai-highlight` class (light blue background). On mouseleave, remove it.

**JS for AI analysis:**
```javascript
async function runAiAnalysis() {
  // Show loading, disable button
  // POST /api/ai-analyze
  // On success: render markdown with marked.parse(), attach hover listeners
  // On error: show error + retry button
}
```

**CSS additions:**
- Tab bar: `.tab-bar`, `.tab-btn`, `.tab-btn.active` (Google-style underline indicator)
- AI report: `.ai-report-container`, `.ai-loading`, `.ai-error`, `.ai-badge`
- Hover highlight: `tr.ai-highlight td { background: rgba(26,115,232,0.08); transition: background 0.2s; }`
- Loading spinner: CSS-only animation (keyframes rotate)

**Acceptance Criteria:**
- [ ] 3 tabs visible above table: 基础信息 | 流量转化 | 成本结构
- [ ] Clicking a tab switches displayed columns without API call
- [ ] Default tab is 基础信息
- [ ] Each tab shows correct column set per spec
- [ ] AI analysis button in sidebar triggers POST to `/api/ai-analyze`
- [ ] Loading spinner shows during API call
- [ ] Markdown report renders correctly in sidebar (marked.js)
- [ ] Report has "DeepSeek AI" badge
- [ ] Hovering product references in report highlights corresponding table row
- [ ] Error state shows friendly message + retry button
- [ ] Table rows have `data-sku` attributes for linking

---

### Step 5: Integration, Polish, and Verification

**All files:** Final pass across all changes.

**Integration tasks:**
1. Verify filter changes trigger: table reload + dashboard charts update + clear AI report (stale)
2. Verify scrape completion triggers: data reload + dashboard update
3. Verify AI report persists across tab switches (report is in sidebar, independent of table tabs)
4. Verify accordion state persists during data operations (no layout jumps)

**Polish tasks:**
1. CSS transitions on all interactive elements:
   - Accordion expand/collapse: smooth `max-height` + `opacity`
   - Tab switch: underline slide animation
   - KPI card values: number count-up on load (optional, nice-to-have)
   - Chart appear: fade-in on first render
   - Table row hover: existing style preserved
2. Empty states:
   - No data: "加载中..." then "没有符合条件的商品"
   - AI report area before first analysis: subtle prompt text "点击上方按钮开始 AI 分析"
   - Chart area before data: placeholder text
3. Responsive tweaks (desktop only, per non-goal):
   - Dashboard charts stack if main area < 800px width
   - Sidebar fixed width 340px (slightly wider than current 320px to accommodate AI report)

**Verification checklist (maps to AC1-AC6):**
- [ ] **AC1**: 5 KPI cards visible at top, values match filtered data, color-coded
- [ ] **AC2**: Pie chart shows category distribution, scatter shows margin vs sales. Both interactive (Chart.js tooltips). Charts update on filter change.
- [ ] **AC3**: 3 tabs work, each shows correct columns, sorting works within each tab
- [ ] **AC4**: AI button -> loading -> report renders in sidebar. Hover product name -> table row highlights. Error -> retry button works.
- [ ] **AC5**: Table badges show Excel/爬取/Demo. AI report area has "DeepSeek AI" badge.
- [ ] **AC6**: Google colors consistent. No layout jumps. Accordion smooth. Tab switch smooth. Overall "demo-ready" impression.

**Acceptance Criteria:**
- [ ] All 6 AC criteria pass
- [ ] Page loads without JS console errors
- [ ] Existing scraper flow works (demo mode test)
- [ ] Filter changes propagate to all UI components (stats, charts, table)
- [ ] AI report survives tab switches and filter changes (until new analysis is run)
- [ ] No regressions in existing functionality

---

## Success Criteria

1. All 6 acceptance criteria from spec pass visual and functional verification
2. `python main.py` starts without errors, page loads at `http://127.0.0.1:5001`
3. Existing scraper demo mode still works end-to-end
4. AI analysis produces a meaningful Markdown report (requires valid DeepSeek API key in config)
5. No changes to `scraper.py` or `parser.py`
6. Page feels "demo-ready" -- smooth transitions, professional layout, no obvious bugs

## Estimated Effort

| Step | Effort | Primary File(s) |
|------|--------|-----------------|
| Step 1: Config + deps | LOW | config.yaml, requirements.txt |
| Step 2: AI module + routes | MEDIUM | ai_analyzer.py (new), main.py |
| Step 3: Layout + dashboard | HIGH | templates/index.html |
| Step 4: Tabs + AI panel | HIGH | templates/index.html |
| Step 5: Integration + polish | MEDIUM | All files |
| **Total** | **HIGH** | 5 files touched, 1 new |

## Open Questions

See `.omc/plans/open-questions.md` for tracked items.
