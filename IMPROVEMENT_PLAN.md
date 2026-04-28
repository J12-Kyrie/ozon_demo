# Ozon-1688 数据采集增强计划

> 基于 2026-04-28 E2E 实测 + Codex 联合分析

## 当前状态

### 已实现 ✅
| 组件 | 状态 | 指标 |
|------|------|------|
| Ozon 列表页爬取 | ✅ CDP 模式 | 136 产品/类目, 5 页 |
| Parser 6 关键字段 | ✅ | benchmark 84.8% |
| JSON-LD 类目提取 | ✅ | 一级/三级类目 100% |
| 评论数/标题 | ✅ | 100% via 俄文正则 |
| Detail enrichment | ✅ | TopK=150, 并发 3, 俄文匹配 |
| merge backfill | ✅ | Excel null → scraped 回填 |
| Estimator v2 | ✅ | 10 个 _est 列 |
| 1688 CSV 映射 | ✅ | 7 个 SKU 手动维护 |

### 阻塞 ⚠️
| 问题 | 根因 | 方案 |
|------|------|------|
| 1688 搜索页 | 验证码 | 用户手动在浏览器打开商品页 → CDP 读取 |
| 1688 详情页 | 需要登录态 | 用户在浏览器登录后 → 脚本从已有 tab 读取 |
| Ozon 店铺名 | 列表页无 data-seller | 需详情页提取, TopK=150 已覆盖 |
| 跟卖 22.1% | 原 TopK=30 | 已改为 150, 待重测 |

---

## 实施路线图

### Phase A: 1688 手动辅助爬取（立即实施）

用户操作:
1. 在 Edge 中打开 https://www.1688.com/ 并登录
2. 手工搜索目标商品关键词
3. 打开第一个商品详情页
4. 运行脚本: `python scripts/scrape_1688_tab.py` — 从当前 tab 提取采购价/运费

脚本实现:
- `scripts/scrape_1688_tab.py` — 连接 CDP, 找到 1688 详情页 tab
- 提取: 价格(¥), 运费, 起批量, 供应商名
- 输出到 CSV/JSON, 供 supplier_mapping.csv 参考

### Phase B: Ozon 详情页增强（部分实施）

| 字段 | 俄文 | 提取方式 | 状态 |
|------|------|---------|------|
| 品牌 | Бренд | detail label-value | ✅ 已实现 |
| 店铺名 | Продавец | detail text | ✅ 已实现 |
| 配送 | Доставка/Ozon | text search | ✅ 已实现 |
| 重量 | Вес товара | regex кг/г | ✅ 已实现 |
| 尺寸 | Длина×Ширина×Высота | regex mm/cm | ✅ 已实现 |
| 评分(精确) | data-rating | detail DOM | ⚠️ 需页面级 JSON |
| 描述 | Описание | detail text block | ❌ 未实现 |
| 变体数 | Варианты | detail selector | ❌ 未实现 |

### Phase C: 数学建模增强

| 模型 | 输入 | 公式 | 状态 |
|------|------|------|------|
| 月销量_est | 评论数, 评分, 竞争 | reviews × rate × rating_factor × competition_factor | ✅ |
| 转化率_est | 评分, 评论, 价格, 竞争 | 4-维度加权 | ✅ |
| 采购价_est | 绿标价, 类目 | price × category_ratio / fx | ✅ (1688 数据替代) |
| 物流费_est | 计费重, 配送方式 | base + per_kg × ceil(weight) | ✅ |
| 佣金_est | 绿标价, 类目 | price × category_rate | ✅ (10 类目费率) |
| 总成本_est | 采购+运费+物流+佣金+推广 | 求和 | ✅ |
| 利润率_est | 绿标价, 总成本 | (price - cost) / price | ✅ |

### Phase D: 数据质量闭环

```mermaid
flowchart LR
    scrape[Ozon爬取] --> parse[解析]
    parse --> enrich[1688补全]
    enrich --> estimate[模型预估]
    estimate --> score[评分排序]
    score --> feedback[字段填充率监控]
    feedback --> scrape
```

- 每次 scrape 后输出字段填充率日志
- `_est` 列标记 `estimate_confidence` 置信度
- 当 1688 真实数据存在时, 自动替换 _est 值

---

## 立即修复项

### 1. 1688 tab scraper 脚本
从用户浏览器当前 1688 标签页提取数据

### 2. Ozon 详情评分精度
从 detail 页 data-rating / JSON-LD Product 获取精确评分

### 3. 评分管道 use _est fallback
当前 filter_and_score 已用 _with_est_fallback, 确认所有评分维度覆盖
