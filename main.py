"""Ozon 选品 MVP Demo — Web GUI + Real-time Scraping"""

import os
import glob
import json
import logging
import asyncio
import threading
import time
import yaml
import pandas as pd
import numpy as np
from flask import (
    Flask,
    render_template,
    jsonify,
    request,
    Response,
    stream_with_context,
)
from parser import is_missing_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
MERGED_OUTPUT_PATH = os.path.join(BASE_DIR, "scraped_merged.xlsx")
STABLE_FIELDS_PATH = os.path.join(BASE_DIR, "stable_fields.yml")
STABLE_STATE_PATH = os.path.join(BASE_DIR, "stable_fields_state.json")

# --------------- data loading ---------------


def find_excel():
    """Find the first .xls/.xlsx in BASE_DIR."""
    for pattern in ("*.xls", "*.xlsx"):
        files = glob.glob(os.path.join(BASE_DIR, pattern))
        if files:
            return files[0]
    return None


def load_data():
    path = find_excel()
    if not path:
        return pd.DataFrame()
    df = pd.read_excel(path)
    df.columns = [c.strip() for c in df.columns]
    df["data_source"] = "excel"
    return df


# Cache raw data at startup
RAW_DF = load_data()
SCRAPE_LOCK = threading.Lock()

# Scrape status (shared between thread and request handlers)
SCRAPE_STATUS = {"running": False, "progress": {}, "error": None, "result_count": 0}
APP_START_TIME = time.time()
APP_VERSION = "1.0.0"

TAB_BASIC_COLS = [
    "SkuId",
    "评级",
    "品牌",
    "店铺名称",
    "一级类目",
    "三级类目",
    "绿标价",
    "黑标价",
    "月销量",
    "月销售额",
    "商品链接",
]

TAB_TRAFFIC_COLS = [
    "SkuId",
    "展示至下单转化率",
    "商品卡加购率",
    "搜索至加入购物车转化率",
    "详情页访问量",
    "曝光次数",
    "销售动态占比",
]

TAB_COST_COLS = [
    "SkuId",
    "阿里巴巴采购价(预)",
    "自定义1688运费金额",
    "ozon物流费(预)",
    "平台佣金(预)",
    "佣金比例%",
    "总成本",
    "成本占比",
    "毛利率",
    "利润(预)",
]

ALL_DATA_COLS = list(dict.fromkeys(TAB_BASIC_COLS + TAB_TRAFFIC_COLS + TAB_COST_COLS))


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def persist_merged_data(df: pd.DataFrame):
    """Persist merged dataset to local Excel for cross-restart recovery."""
    if df.empty:
        return
    df.to_excel(MERGED_OUTPUT_PATH, index=False)


def _load_stable_fields_config():
    if not os.path.exists(STABLE_FIELDS_PATH):
        return {"success_streak_required": 2, "fields": []}
    with open(STABLE_FIELDS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"success_streak_required": 2, "fields": []}


def _load_stable_state():
    if not os.path.exists(STABLE_STATE_PATH):
        return {}
    try:
        with open(STABLE_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_stable_state(state):
    with open(STABLE_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _update_stable_fields_state(df: pd.DataFrame):
    cfg = _load_stable_fields_config()
    fields = cfg.get("fields", [])
    streak_required = int(cfg.get("success_streak_required", 2))
    if df.empty or not fields:
        return

    state = _load_stable_state()
    for _, row in df.iterrows():
        sku = row.get("SkuId")
        if pd.isna(sku):
            continue
        sku_key = str(int(sku))
        for field in fields:
            key = f"{sku_key}::{field}"
            item = state.get(key, {"success_streak": 0, "stable": False})
            value = row.get(field)
            if is_missing_value(value):
                item["success_streak"] = 0
                item["stable"] = False
            else:
                item["success_streak"] = int(item.get("success_streak", 0)) + 1
                item["stable"] = item["success_streak"] >= streak_required
                item["last_value"] = value
            state[key] = item
    _save_stable_state(state)


# --------------- merge ---------------


def merge_dataframes(existing: pd.DataFrame, scraped: pd.DataFrame) -> pd.DataFrame:
    """Merge scraped data into existing, deduplicating by SkuId. Excel rows win."""
    if scraped.empty:
        return existing.copy()
    if existing.empty:
        return scraped.copy()

    # Keep only scraped rows whose SkuId is NOT already in existing
    existing_skus = set(existing["SkuId"].dropna().astype(int).tolist())
    new_rows = scraped[~scraped["SkuId"].astype(int).isin(existing_skus)]

    merged = pd.concat([existing, new_rows], ignore_index=True)
    return merged


# --------------- filtering & scoring ---------------


def _safe_filter(series, op, threshold):
    """Apply filter but let NaN rows pass (they lack the data, not fail the check)."""
    if op == ">=":
        return series.isna() | (series >= threshold)
    elif op == "<=":
        return series.isna() | (series <= threshold)
    return pd.Series(True, index=series.index)


def _with_est_fallback(df: pd.DataFrame, base_col: str) -> pd.Series:
    """Fallback to *_est only when base column is missing (0 is valid)."""
    base = df[base_col] if base_col in df.columns else pd.Series(np.nan, index=df.index)
    est_col = f"{base_col}_est"
    if est_col not in df.columns:
        return base
    return base.combine_first(df[est_col])


def apply_filters(cfg, df):
    """Apply config filters to df, returning (filtered_df, total_before). Does not score."""
    df = df.copy()
    f = cfg["filters"]
    df = df[_safe_filter(df["月销量"], ">=", f["min_monthly_sales"])]
    df = df[_safe_filter(df["毛利率"], ">=", f["min_gross_margin"])]
    df = df[_safe_filter(df["跟卖数量"], "<=", f["max_competitors"])]
    df = df[_safe_filter(df["阿里巴巴采购价(预)"], "<=", f["max_purchase_price"])]
    df = df[_safe_filter(df["绿标价"], ">=", f["min_selling_price"])]
    if f.get("categories"):
        df = df[df["一级类目"].isna() | df["一级类目"].isin(f["categories"])]
    return df


def filter_and_score(cfg, df=None):
    """Filter and score products. If df is None, uses global RAW_DF."""
    if df is None:
        with SCRAPE_LOCK:
            df = RAW_DF.copy()
    else:
        df = df.copy()

    if df.empty:
        return df, {}

    total_before = len(df)
    df = apply_filters(cfg, df)
    if "data_source" in df.columns:
        df = df[df["data_source"] == "scraped"].copy()
    total_after = len(df)
    if total_after == 0:
        return df, {"total_before": total_before, "total_after": 0}

    # Normalize each scoring dimension to [0, 1], filling NaN with 0
    def norm(series):
        s = series.fillna(0)
        mn, mx = s.min(), s.max()
        if mx == mn:
            return pd.Series(0.5, index=s.index)
        return (s - mn) / (mx - mn)

    w = cfg["scoring"]
    df = df.copy()
    margin_s = _with_est_fallback(df, "毛利率")
    sales_s = _with_est_fallback(df, "月销量")
    conv_s = _with_est_fallback(df, "展示至下单转化率")
    comp_s = _with_est_fallback(df, "跟卖数量")
    df["_s_margin"] = norm(margin_s) * w["gross_margin"]
    df["_s_sales"] = norm(sales_s) * w["monthly_sales"]
    df["_s_conv"] = norm(conv_s) * w["conversion_rate"]
    df["_s_comp"] = (1 - norm(comp_s)) * w["low_competition"]
    df["综合评分"] = df["_s_margin"] + df["_s_sales"] + df["_s_conv"] + df["_s_comp"]
    df["综合评分"] = (df["综合评分"] * 100).round(1)

    df = df.sort_values("综合评分", ascending=False)

    top_n = cfg["output"]["top_n"]
    df = df.head(top_n)

    show_cols = [c for c in ALL_DATA_COLS if c in df.columns] + ["综合评分"]
    if "data_source" in df.columns:
        show_cols.append("data_source")
    df = df[show_cols].copy()

    # Round floats
    for col in df.select_dtypes(include=[np.floating]).columns:
        df[col] = df[col].round(2)

    stats = {
        "total_before": total_before,
        "total_after": total_after,
        "shown": len(df),
        "avg_margin": round(df["毛利率"].dropna().mean(), 1)
        if df["毛利率"].dropna().any()
        else 0,
        "avg_sales": round(df["月销量"].dropna().mean(), 1)
        if df["月销量"].dropna().any()
        else 0,
        "avg_score": round(df["综合评分"].mean(), 1) if not df.empty else 0,
    }
    return df, stats


# --------------- scraper thread ---------------


def _run_scrape_thread(url: str, config: dict, demo: bool = False):
    """Background thread: run Playwright scrape (or demo), merge results into RAW_DF."""
    global RAW_DF
    SCRAPE_STATUS["running"] = True
    SCRAPE_STATUS["error"] = None
    SCRAPE_STATUS["progress"] = {
        "status": "starting",
        "current_page": 0,
        "total_pages": 0,
        "products_found": 0,
    }

    try:

        def on_progress(info):
            SCRAPE_STATUS["progress"] = {**info, "status": "scraping"}

        if demo:
            from scraper import run_demo_scrape, _normalize_demo_product

            records = run_demo_scrape(progress_callback=on_progress)
            normalized = [_normalize_demo_product(r) for r in records]
        else:
            from scraper import run_scrape, OzonBlockedError

            try:
                records = run_scrape(
                    url, config.get("scraper", {}), progress_callback=on_progress
                )
            except OzonBlockedError as e:
                SCRAPE_STATUS["error"] = str(e)
                SCRAPE_STATUS["progress"]["status"] = "blocked"
                return
            from parser import normalize_product

            normalized = [normalize_product(r) for r in records]

        SCRAPE_STATUS["progress"]["status"] = "merging"
        SCRAPE_STATUS["result_count"] = len(normalized)

        if normalized:
            from supplier_1688 import enrich_products_with_supplier
            from estimator import apply_estimates

            normalized, supplier_stats = enrich_products_with_supplier(
                normalized, config
            )
            scraped_df = pd.DataFrame(normalized)
            with SCRAPE_LOCK:
                merged = merge_dataframes(RAW_DF, scraped_df)
                merged = apply_estimates(merged, config)
                RAW_DF = merged
                _update_stable_fields_state(RAW_DF)
                try:
                    persist_merged_data(RAW_DF)
                    log.info("Persisted merged data to %s", MERGED_OUTPUT_PATH)
                except Exception as e:
                    log.error("Failed to persist merged data: %s", e)
            total_rows = len(RAW_DF) if not RAW_DF.empty else 0
            if total_rows:
                real_margin = (
                    RAW_DF["毛利率"].notna().sum() if "毛利率" in RAW_DF.columns else 0
                )
                est_margin = (
                    RAW_DF["毛利率_est"].notna().sum()
                    if "毛利率_est" in RAW_DF.columns
                    else 0
                )
                real_conv = (
                    RAW_DF["展示至下单转化率"].notna().sum()
                    if "展示至下单转化率" in RAW_DF.columns
                    else 0
                )
                est_conv = (
                    RAW_DF["展示至下单转化率_est"].notna().sum()
                    if "展示至下单转化率_est" in RAW_DF.columns
                    else 0
                )
                log.info(
                    "Field fill rate margin(real=%s/%s est=%s/%s) conv(real=%s/%s est=%s/%s)",
                    real_margin,
                    total_rows,
                    est_margin,
                    total_rows,
                    real_conv,
                    total_rows,
                    est_conv,
                    total_rows,
                )
            log.info(
                "Supplier stats hit=%s total=%s miss=%s manual=%s",
                supplier_stats.get("mapping_hit", 0),
                supplier_stats.get("mapping_total", 0),
                supplier_stats.get("mapping_miss", 0),
                supplier_stats.get("manual_required", 0),
            )

        SCRAPE_STATUS["progress"]["status"] = "done"

    except Exception as e:
        SCRAPE_STATUS["error"] = str(e)
        SCRAPE_STATUS["progress"]["status"] = "error"
    finally:
        SCRAPE_STATUS["running"] = False


# --------------- error handlers ---------------


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    log.error("Internal server error: %s", e)
    return jsonify({"error": "Internal server error"}), 500


@app.after_request
def log_request(response):
    if request.path.startswith("/api/"):
        log.info("%s %s → %s", request.method, request.path, response.status_code)
    return response


# --------------- routes ---------------


@app.route("/api/health")
def api_health():
    uptime = int(time.time() - APP_START_TIME)
    with SCRAPE_LOCK:
        product_count = len(RAW_DF)
    return jsonify(
        {
            "status": "ok",
            "version": APP_VERSION,
            "uptime_seconds": uptime,
            "products_loaded": product_count,
        }
    )


@app.route("/favicon.ico")
def favicon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#1a73e8"/><text x="16" y="23" text-anchor="middle" font-size="20" fill="#fff" font-family="sans-serif" font-weight="700">O</text></svg>'
    return Response(svg, mimetype="image/svg+xml")


@app.route("/")
def index():
    cfg = load_config()
    with SCRAPE_LOCK:
        df = RAW_DF.copy()
    categories = (
        sorted(df["一级类目"].dropna().unique().tolist()) if not df.empty else []
    )
    return render_template("index.html", config=cfg, categories=categories)


@app.route("/api/data")
def api_data():
    cfg = load_config()
    df, stats = filter_and_score(cfg)
    records = df.to_dict(orient="records") if not df.empty else []
    return jsonify({"data": records, "stats": stats})


@app.route("/api/config", methods=["POST"])
def api_config():
    payload = request.get_json(force=True)
    cfg = load_config()

    for key in ("min_monthly_sales", "max_competitors"):
        if key in payload:
            cfg["filters"][key] = int(payload[key])
    for key in ("min_gross_margin", "max_purchase_price", "min_selling_price"):
        if key in payload:
            cfg["filters"][key] = float(payload[key])
    if "categories" in payload:
        cfg["filters"]["categories"] = payload["categories"]

    for key in ("gross_margin", "monthly_sales", "conversion_rate", "low_competition"):
        if key in payload:
            cfg["scoring"][key] = float(payload[key])

    if "top_n" in payload:
        cfg["output"]["top_n"] = int(payload["top_n"])

    save_config(cfg)

    df, stats = filter_and_score(cfg)
    records = df.to_dict(orient="records") if not df.empty else []
    return jsonify({"data": records, "stats": stats})


@app.route("/api/dashboard-data")
def api_dashboard_data():
    cfg = load_config()
    df, stats = filter_and_score(cfg)

    cat_counts = {}
    if not df.empty and "一级类目" in df.columns:
        cat_counts = df["一级类目"].dropna().value_counts().to_dict()

    scatter = []
    if not df.empty and "毛利率" in df.columns and "月销量" in df.columns:
        for _, row in df.iterrows():
            margin = row.get("毛利率")
            sales = row.get("月销量")
            if margin is not None and sales is not None:
                scatter.append(
                    {
                        "sku": str(row.get("SkuId", "")),
                        "name": str(row.get("三级类目", "")),
                        "margin": float(margin) if pd.notna(margin) else 0,
                        "sales": float(sales) if pd.notna(sales) else 0,
                    }
                )

    return jsonify(
        {
            "categories": cat_counts,
            "scatter": scatter,
            "kpi": stats,
        }
    )


@app.route("/api/ai-analyze", methods=["POST"])
def api_ai_analyze():
    from ai_analyzer import prepare_ai_payload, generate_report

    cfg = load_config()
    with SCRAPE_LOCK:
        df = RAW_DF.copy()
    if df.empty:
        return jsonify({"error": "没有数据，请先加载 Excel 或爬取"})

    df = apply_filters(cfg, df)

    if df.empty:
        return jsonify({"error": "没有筛选出商品，请调整筛选条件"})

    df = df.sort_values("毛利率", ascending=False, na_position="last")
    max_products = cfg.get("ai", {}).get("max_products", 20)
    payload = prepare_ai_payload(df, max_products)
    result = generate_report(cfg, payload)
    if "error" in result:
        log.warning("AI analyze failed but ignored: %s", result["error"])
        return jsonify(
            {
                "report": "AI 分析暂不可用（已忽略接口错误）",
                "product_ids": [],
                "ignored_error": result["error"],
            }
        )

    if "report" in result:
        sku_ids = df.head(max_products)["SkuId"].dropna().astype(str).tolist()
        result["product_ids"] = sku_ids

    return jsonify(result)


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """Start a scrape in background thread. Pass demo=true for demo mode."""
    if SCRAPE_STATUS["running"]:
        return jsonify({"error": "A scrape is already running"}), 409

    payload = request.get_json(force=True)
    demo = payload.get("demo", False)
    url = payload.get("url", "").strip()
    keyword = payload.get("keyword", "").strip()

    if not demo and not url and not keyword:
        return jsonify({"error": "Provide url, keyword, or use demo mode"}), 400

    if keyword and not url:
        url = f"https://www.ozon.ru/search/?text={keyword}"

    cfg = load_config()
    thread = threading.Thread(
        target=_run_scrape_thread, args=(url, cfg, demo), daemon=True
    )
    thread.start()

    mode = "demo" if demo else "live"
    return jsonify({"status": "started", "url": url, "mode": mode})


@app.route("/api/scrape/status")
def api_scrape_status():
    """Polling endpoint for scrape status."""
    return jsonify(SCRAPE_STATUS)


@app.route("/api/scrape/stream")
def api_scrape_stream():
    """SSE endpoint for real-time scrape progress."""

    def generate():
        while True:
            data = json.dumps(SCRAPE_STATUS)
            yield f"data: {data}\n\n"
            if not SCRAPE_STATUS["running"] and SCRAPE_STATUS["progress"].get(
                "status"
            ) in ("done", "error", ""):
                break
            time.sleep(0.5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------- main ---------------

if __name__ == "__main__":
    log.info("Loaded %d products from Excel", len(RAW_DF))
    if not RAW_DF.empty:
        log.info(
            "Categories: %s", sorted(RAW_DF["一级类目"].dropna().unique().tolist())
        )
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5001"))
    log.info("Open http://%s:%d", host, port)
    app.run(host=host, port=port, debug=True, use_reloader=False)
