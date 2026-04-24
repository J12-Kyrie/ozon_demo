# Deep Interview Spec: Ozon Demo 产品升级

## Metadata
- Interview ID: di-ozon-product-upgrade-20260421
- Rounds: 18
- Final Ambiguity Score: 5.1%
- Type: brownfield
- Generated: 2026-04-21
- Threshold: 20%
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.97 | 0.35 | 0.340 |
| Constraint Clarity | 0.93 | 0.25 | 0.233 |
| Success Criteria | 0.95 | 0.25 | 0.238 |
| Context Clarity | 0.93 | 0.15 | 0.140 |
| **Total Clarity** | | | **0.949** |
| **Ambiguity** | | | **5.1%** |

## Goal
将现有 Ozon 选品 Demo（Flask + 纯表格 UI）升级为可演示级产品：采用仪表盘+表格混合布局，展示 Excel 全部核心业务数据（品牌店铺 + 流量转化 + 成本结构），集成 DeepSeek AI 批量分析报告（商业决策向），保持 Google 色调设计语言。

## Constraints
- **DeepSeek API**: 已有 API Key，使用 deepseek-chat 模型控制成本
- **API Key 存储**: 存入 config.yaml 的 `ai.api_key` 字段（用户选择简洁方案，注意 .gitignore）
- **成本控制**: 批量分析筛选后的 TOP N 商品（默认 20），而非全部 100 条
- **技术栈**: 保持 Flask 单页应用，前端无额外框架（纯 HTML/CSS/JS + Chart.js CDN）
- **Google 色调**: 延续现有 CSS 变量体系（--accent: #1a73e8, --green: #188038 等）
- **布局**: 仪表盘(KPI卡片+图表) + 表格(列分组Tab切换) + AI报告(Sidebar内嵌)
- **Sidebar 重组**: 所有区域改为 Accordion 可折叠分组，默认展开：筛选+AI分析，默认收起：权重+爬取
- **AI 联动高亮**: 报告中提到的商品 hover 时，表格对应行整行高亮（淡蓝背景）
- **数据源标记**: 表格保留原有来源标记（Excel/爬取），AI 报告区域单独标记为 "DeepSeek AI"
- **AI 交互模式**: 一次性返回 + Loading 动画（非流式），简化前端实现
- **AI Prompt 输入**: 精简核心字段 ~10 个（SkuId, 一级类目, 三级类目, 绿标价, 月销量, 毛利率, 利润(预), 跟卖数量, 展示至下单转化率, 阿里巴巴采购价(预)），约 4K token/20 商品
- **AI 报告渲染**: 前端使用 marked.js CDN 将 Markdown 渲染为 HTML
- **AI 错误处理**: 失败时显示友好错误提示 + "重试"按钮
- **兼容性**: 保留现有爬虫功能不破坏，新增 AI 分析为独立功能

## Non-Goals
- 不重写爬虫模块（scraper.py / parser.py 保持不变）
- 不引入前端框架（React/Vue）
- 不实现用户认证/多用户
- 不做移动端适配
- 不实现 AI 逐条商品分析弹窗（本次只做批量报告）

## Acceptance Criteria
- [ ] **AC1**: 仪表盘 KPI 卡片 — 页面顶部显示 4+ 个关键指标卡片（原始商品数、筛选通过数、平均毛利率、平均月销量、平均评分等），带数值和颜色区分
- [ ] **AC2**: 仪表盘图表 — 2 个交互式图表：①一级类目商品数量饼图 ②毛利率 vs 月销量散点图（找"明星商品"），使用 Chart.js
- [ ] **AC3**: 表格列分组 — 表格支持 Tab 切换查看不同维度数据：基础信息 | 流量转化 | 成本结构，而非一次展示全部 30+ 列
- [ ] **AC4**: AI 分析报告 — Sidebar 内"AI分析"按钮 → 调用 DeepSeek API（Loading动画）→ Sidebar 内渲染 Markdown 报告（marked.js）。报告中商品名 hover 时表格对应行高亮
- [ ] **AC5**: 数据来源标记 — 表格保留原有 Excel/爬取 badge，AI 报告区域标记为 "DeepSeek AI"
- [ ] **AC6**: Google 色调 + 动画流畅 — 保持现有色彩体系，页面切换/数据加载有平滑过渡动画，无明显 bug，达到可演示水平

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| 纯表格不够"产品化" | 表格对选品场景高效（Contrarian） | 保留表格但加仪表盘层，两者混合 |
| 需要逐条AI分析 | 成本和复杂度高 | 改为批量报告模式，分析 TOP N 商品 |
| 需要所有37列可视化 | 信息过载 | 选择 A+B+C 核心业务组（~30列），Tab分组展示 |
| 需要 deepseek-reasoner | 成本高 | 商业决策向使用 deepseek-chat 足够 |
| 4个方向需要排优先级 | 工作量大（Simplifier） | 用户坚持全部并行交付 |

## Technical Context (Brownfield)

### Current Architecture
```
main.py (Flask routes) → parser.py (HTML parsing) → scraper.py (Playwright/CDP)
templates/index.html (单页应用, 纯CSS, ~506行)
config.yaml (筛选+爬虫配置)
Excel: 37列, 100条产品
```

### Files to Modify
1. **main.py** — 新增 `/api/ai-analyze` 路由（调用 DeepSeek API），修改 KEY_COLS 加入 A+B+C 列
2. **templates/index.html** — 重写布局：仪表盘区域(KPI+Chart.js图表) + Tab分组表格 + AI报告折叠面板，添加 Chart.js CDN
3. **config.yaml** — 新增 `ai` 配置区块（model, max_products, prompt_template）
4. **requirements.txt** — 新增 `openai`（DeepSeek 兼容 OpenAI SDK）

### New Components
- DeepSeek API 调用模块（可内联在 main.py 或独立 ai_analyzer.py）
- Chart.js 图表（类目分布、毛利率分布）
- Tab 切换组件（基础信息 / 流量转化 / 成本结构）
- AI 报告折叠面板 + Loading 动画（一次性返回，非流式）

### Data Column Groups
**Tab 1 — 基础信息（现有+扩展A）:**
SkuId, 评级, 品牌, 店铺名称, 一级类目, 三级类目, 绿标价, 黑标价, 月销量, 月销售额, 综合评分, 商品链接

**Tab 2 — 流量转化（B组）:**
SkuId, 展示至下单转化率, 商品卡加购率, 搜索至加购转化率, 详情页访问量, 曝光次数, 销售动态占比

**Tab 3 — 成本结构（C组）:**
SkuId, 阿里巴巴采购价(预), 1688运费, ozon物流费(预), 平台佣金(预), 佣金比例%, 总成本, 成本占比, 毛利率, 利润(预)

### AI Report Structure
DeepSeek prompt 输入：筛选后 TOP N 商品的关键指标 JSON
输出格式（Markdown）:
1. **TOP 推荐** — 最值得跟卖的 3-5 个商品及理由
2. **风险提示** — 高竞争/低毛利/价格异常的商品预警
3. **类目趋势** — 各类目销售表现总结
4. **价格策略建议** — 基于成本结构的定价建议

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| Product | core domain | SkuId, 评级, 品牌, 价格, 销量, 毛利率... | belongs to Category, sold by Shop |
| Dashboard | core UI | KPI cards, charts | displays Product aggregations |
| DataTable | core UI | tabs, columns, sorting | displays Product details |
| BatchReport | core domain | TOP推荐, 风险提示, 趋势, 策略 | analyzes filtered Products |
| DeepSeek API | external system | model, api_key, endpoint | generates BatchReport |
| Category | supporting | 一级类目, 三级类目 | groups Products |
| Shop | supporting | 店铺Id, 店铺名称 | sells Products |
| CostStructure | supporting | 采购价, 运费, 物流费, 佣金 | part of Product |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1-3 | 5 | 5 | - | - | N/A |
| 4-5 | 7 | 2 | 0 | 5 | 71% |
| 6-7 | 8 | 1 | 0 | 7 | 88% |
| 8 | 8 | 0 | 0 | 8 | 100% |
| 9-11 | 8 | 0 | 0 | 8 | 100% |
| 12 | 9 | 1 | 0 | 8 | 89% |
| 13-15 | 9 | 0 | 0 | 9 | 100% |
| 16-18 | 10 | 1 | 0 | 9 | 90% |

## Interview Transcript
<details>
<summary>Full Q&A (8 rounds)</summary>

### Round 1
**Q:** 你提到了 4 个方向（关键数据可视化、DeepSeek AI分析、HTML布局优化、生产化）。如果只能先做一个，哪个对你最有价值？
**A:** 全部并行
**Ambiguity:** 85% (Goal: 0.3, Constraints: 0.2, Criteria: 0.1)

### Round 2
**Q:** DeepSeek AI 分析的具体输出是什么形态？
**A:** 批量分析报告
**Ambiguity:** 72% (Goal: 0.45, Constraints: 0.2, Criteria: 0.1)

### Round 3
**Q:** 从Excel的37列中，哪些应该加入UI可视化？
**A:** A+B+C (核心业务) — 品牌店铺+流量转化+成本结构
**Ambiguity:** 64.5% (Goal: 0.60, Constraints: 0.20, Criteria: 0.10)

### Round 4
**Q:** DeepSeek API 的调用方式和约束是什么？
**A:** 已有Key，需控制成本
**Ambiguity:** 54.0% (Goal: 0.60, Constraints: 0.45, Criteria: 0.10)

### Round 5 [CONTRARIAN MODE]
**Q:** 现在的纯表格布局其实对「选品」场景很高效，你确定要改吗？
**A:** 仪表盘 + 表格混合
**Ambiguity:** 48.0% (Goal: 0.75, Constraints: 0.45, Criteria: 0.10)

### Round 6 [SIMPLIFIER MODE]
**Q:** 你看到什么结果会说「这就是我要的」？
**A:** 可演示级别
**Ambiguity:** 36.3% (Goal: 0.80, Constraints: 0.45, Criteria: 0.50)

### Round 7
**Q:** AI 批量分析报告应该包含哪些分析维度？用哪个模型？
**A:** 商业决策向，使用 deepseek-chat
**Ambiguity:** 27.0% (Goal: 0.90, Constraints: 0.65, Criteria: 0.50)

### Round 8
**Q:** 6 条验收标准是否全部必须？
**A:** 全部必须
**Ambiguity:** 17.6% (Goal: 0.92, Constraints: 0.70, Criteria: 0.80)

### Round 9
**Q:** AI 报告生成时，用户体验应该是怎样的？
**A:** 一次性返回 + Loading（非流式）
**Ambiguity:** 15.6% (Goal: 0.92, Constraints: 0.78, Criteria: 0.80)

### Round 10
**Q:** "来源替换为AI预测"具体指什么？
**A:** 保留原标记 + AI报告单独标记为 "DeepSeek AI"
**Ambiguity:** 14.0% (Goal: 0.95, Constraints: 0.78, Criteria: 0.82)

### Round 11
**Q:** DeepSeek API Key 怎么传入应用？
**A:** config.yaml（简单直接）
**Ambiguity:** 12.3% (Goal: 0.95, Constraints: 0.85, Criteria: 0.82)

### Round 12
**Q:** 仪表盘上的 2 个图表具体展示什么？
**A:** 类目分布饼图 + 毛利率vs月销量散点图
**Ambiguity:** 10.3% (Goal: 0.95, Constraints: 0.85, Criteria: 0.90)

### Round 13
**Q:** 调用 DeepSeek 时，应该把哪些字段发给 AI 分析？
**A:** 精简核心字段 (~10个)，约 4K token/20商品
**Ambiguity:** 8.6% (Goal: 0.95, Constraints: 0.90, Criteria: 0.90)

### Round 14
**Q:** AI 报告的内容渲染方式？
**A:** marked.js CDN 渲染 Markdown 为 HTML
**Ambiguity:** 7.5% (Goal: 0.95, Constraints: 0.90, Criteria: 0.93)

### Round 15
**Q:** DeepSeek API 调用失败时如何处理？
**A:** 友好提示 + 重试按钮
**Ambiguity:** 6.8% (Goal: 0.95, Constraints: 0.93, Criteria: 0.93)

### Round 16
**Q:** AI 报告在页面上的具体位置？
**A:** Sidebar 内嵌 + 联动高亮
**Ambiguity:** 6.1% (Goal: 0.97, Constraints: 0.93, Criteria: 0.93)

### Round 17
**Q:** 联动高亮的触发机制？
**A:** Hover 高亮整行（淡蓝背景）
**Ambiguity:** 5.6% (Goal: 0.97, Constraints: 0.93, Criteria: 0.95)

### Round 18
**Q:** Sidebar 加入 AI 报告后如何重新组织？
**A:** 可折叠 Accordion 分组，默认展开筛选+AI，收起权重+爬取
**Ambiguity:** 5.1% (Goal: 0.97, Constraints: 0.93, Criteria: 0.95)

</details>
