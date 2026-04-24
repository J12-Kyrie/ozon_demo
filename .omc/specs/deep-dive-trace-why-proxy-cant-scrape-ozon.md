# Deep Dive Trace: why-proxy-cant-scrape-ozon

## Observed Result
curl through IPRoyal Web Unblocker proxy works for simple sites (icanhazip.com returns IP), but Playwright-based Ozon scraper fails with ERR_EMPTY_RESPONSE. User asks: why can't we scrape Ozon?

## Ranked Hypotheses
| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | Web Unblocker can't access Ozon at all — even curl (no Playwright) returns empty | **High** | **Strong** | Reproduced: curl to Ozon → HTTP 000, 0 bytes, 18.8s timeout. Only simple sites work. |
| 2 | Web Unblocker is a MITM rendering proxy incompatible with Playwright by design | **High** | **Strong** | IPRoyal docs explicitly state: "not recommended to use with browsers or Playwright" |
| 3 | Local proxy tunnel has implementation bug causing ERR_EMPTY_RESPONSE | **Refuted** | **Strong (against)** | Corrected async test: tunnel works for simple sites (HTTP 200). Ozon fails = upstream issue. |

## Evidence Summary by Hypothesis

### Hypothesis 1: Web Unblocker can't reach Ozon
- **curl direct (no Playwright, no tunnel):** icanhazip.com → HTTP 200, 15 bytes, 2.8s ✓
- **curl direct (no Playwright, no tunnel):** www.ozon.ru → HTTP 000, 0 bytes, 18.8s ✗
- **curl via local tunnel (async):** icanhazip.com → HTTP 200, 11 bytes, 2.5s ✓
- **curl via local tunnel (async):** www.ozon.ru → HTTP 000, 0 bytes, 29.3s ✗
- Root cause: Web Unblocker's rendering engine/IP is blocked by Ozon's anti-bot, OR Web Unblocker cannot handle Ozon's complexity (heavy JS, anti-scraping measures)

### Hypothesis 2: Product mismatch (MITM rendering proxy vs Playwright)
- **IPRoyal docs (primary source):** "Due to the MITM mechanism and the required SSL handling, it is not recommended to use the unblocker in browsers or with tools like Playwright."
- **TLS certificate artifact:** `issuer: C=LT; O=Test`, `subject: O=testcompany; CN=www.ozon.ru` — synthetic MITM certificate, not pass-through tunneling
- **GET-only constraint:** Web Unblocker only supports GET requests — a pass-through proxy has no such limitation
- **Oxylabs equivalent:** Same product category, same documented limitation: "not designed for headless browsers (Playwright, Selenium, Puppeteer)"
- Architecture: Web Unblocker terminates TLS → renders page internally → returns processed HTML. It IS a browser, not a proxy for browsers.

### Hypothesis 3: Local proxy tunnel bug (REFUTED)
- **Original test was flawed:** Used blocking `subprocess.run()` inside asyncio, preventing event loop from running relay coroutines
- **Corrected async test:** Tunnel works perfectly for icanhazip.com (HTTP 200, 2.5s)
- **Ozon failure via tunnel:** Same as direct curl — HTTP 000, confirming upstream issue, not tunnel bug

## Evidence Against / Missing Evidence
- **Hypothesis 1:** No diagnostic info from Web Unblocker about WHY Ozon fails (their rendering log is opaque)
- **Hypothesis 2:** Already confirmed by vendor documentation — no significant counter-evidence
- **Hypothesis 3:** Fully refuted by corrected test

## Per-Lane Critical Unknowns
- **Lane 1 (Web Unblocker + Ozon):** Whether Web Unblocker's failure on Ozon is due to Ozon blocking Web Unblocker's IP pool, or Web Unblocker's renderer timing out on Ozon's heavy JS
- **Lane 2 (Product mismatch):** Already resolved — documented incompatibility
- **Lane 3 (Tunnel bug):** Already resolved — tunnel works correctly

## Rebuttal Round
- **Best rebuttal to Lane 1:** "Maybe Web Unblocker needs more time for Ozon (heavy JS). Increase timeout to 120s."
- **Why leader held:** curl with 45s timeout still got 0 bytes. Web Unblocker's own rendering has internal timeouts. Even if it did eventually return HTML, it would be pre-rendered static content — unusable by Playwright for dynamic interaction.

## Convergence / Separation Notes
- Lanes 1 and 2 **converge**: Web Unblocker is wrong for this use case on TWO independent axes — it can't reach Ozon AND it's architecturally incompatible with Playwright
- Lane 3 is independent and refuted — the tunnel code works correctly

## Most Likely Explanation
**TWO independent root causes, both fatal:**

1. **Web Unblocker cannot access Ozon.** Even pure curl (no Playwright involved) returns empty response for Ozon while working fine for simple sites. The Web Unblocker's internal renderer/IP is blocked by Ozon's anti-bot.

2. **Web Unblocker is categorically incompatible with Playwright.** IPRoyal's own documentation explicitly states this. Web Unblocker is a server-side rendering proxy (it IS a browser) — combining it with Playwright creates a double-browser conflict. Even if Ozon worked, Playwright would receive pre-rendered static HTML, not a live DOM.

## Critical Unknown
Whether the user's **Dedicated IP proxy** (from their IPRoyal email) has a Russian IP address. A standard Dedicated IP proxy (not Web Unblocker) would be compatible with Playwright — but only a Russian IP can bypass Ozon's geo-restriction.

## Recommended Discriminating Probe
1. Log into IPRoyal Dashboard → check the Dedicated IP proxy's assigned IP address
2. Verify the IP is Russian: `curl -x http://USER:PASS@DEDICATED_IP:12323 https://ipv4.icanhazip.com` then check IP geolocation
3. If Russian: test `curl -x http://USER:PASS@DEDICATED_IP:12323 https://www.ozon.ru/` to confirm Ozon access
