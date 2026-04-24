# Open Questions

## ozon-scraper — 2026-04-15

- [ ] Ozon page structure may change without notice — parser selectors will need maintenance. Should we add a selector config or keep them hardcoded for MVP? — Affects long-term maintainability vs. MVP speed tradeoff.
- [ ] Scraped data lacks several columns present in the Excel file (e.g., 毛利率, 利润(预), 阿里巴巴采购价(预), 展示至下单转化率). How should missing columns be handled during merge? Suggested default: fill with NaN so rows appear in table but score lower. — Affects whether scraped products can be meaningfully scored/ranked alongside Excel data.
- [ ] Ozon may serve different page layouts for different regions or A/B tests. Should the parser handle multiple known layouts or just the primary one? — Determines parser complexity and test fixture count.
- [ ] The current app has no `requirements.txt`. Confirming that `xlrd` is the correct reader for the `.xls` file (not `.xlsx`). — Affects whether `openpyxl` or `xlrd` is the primary Excel dependency.
- [ ] Should scraped data persist across server restarts (e.g., save to a local JSON/CSV file)? Current plan says in-memory only, matching the existing pattern. — User may expect scraped data to survive restarts.

## ozon-product-upgrade — 2026-04-21

- [ ] DeepSeek API key must be added to config.yaml before AI analysis can work. Should the plan include a `.gitignore` entry for config.yaml to prevent accidental key exposure? — Security concern noted in spec (Round 11).
- [ ] The hover-linking between AI report and table rows depends on the AI output containing recognizable product identifiers (SkuId or product names). If DeepSeek uses different naming in its response, hover detection may miss matches. Should we inject explicit SkuId markers in the prompt to ensure matchability? — Affects reliability of AC4 hover-highlight feature.
- [ ] The Excel file has 37 columns. The spec selects ~30 across 3 tab groups. Some column names in the spec (e.g., "搜索至加购转化率") may not exactly match Excel headers (e.g., "搜索至加入购物车转化率" in parser.py). Column name mapping needs verification against actual Excel headers. — Affects whether tab columns render correctly or show "-" for mismatched names.
- [ ] The `filter_and_score()` function currently returns only KEY_COLS subset. Expanding it to return all ~30 columns means more data per API response. For 100 products this is fine, but worth noting. — Minimal performance concern for demo scale.
- [ ] Chart.js and marked.js are loaded via CDN. If the demo runs in an environment without internet access, charts and AI report rendering will break. Should we bundle these as local static files? — Affects offline demo capability.
