"""Tests for the product upgrade: AI analyzer, dashboard API, tab columns."""

import json
import sys
import os

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_row(
    sku_id,
    category="电子产品",
    margin=55.0,
    sales=5,
    price=200.0,
    data_source="scraped",
):
    return {
        "SkuId": sku_id,
        "评级": 4.0,
        "黑标价": price * 1.1,
        "绿标价": price,
        "品牌": "Brand",
        "一级类目": category,
        "三级类目": "Sub",
        "店铺Id": 1,
        "店铺名称": "Shop",
        "月销量": sales,
        "月销售额": sales * price,
        "展示至下单转化率": 0.5,
        "商品卡加购率": 3.0,
        "搜索至加入购物车转化率": 0.4,
        "详情页访问量": 100,
        "曝光次数": 500,
        "销售动态占比": 80.0,
        "跟卖数量": 8,
        "阿里巴巴采购价(预)": 30.0,
        "自定义1688运费金额": 2.0,
        "ozon物流费(预)": 15.0,
        "平台佣金(预)": price * 0.14,
        "佣金比例%": "14%",
        "总成本": price * 0.45,
        "成本占比": 0.45,
        "毛利率": margin,
        "利润(预)": price * 0.3,
        "商品链接": f"https://ozon.ru/product/{sku_id}",
        "data_source": data_source,
    }


class TestAiAnalyzer:
    def test_prepare_payload_selects_fields(self):
        from ai_analyzer import prepare_ai_payload, AI_FIELDS

        df = pd.DataFrame([_make_row(1001), _make_row(1002)])
        payload = prepare_ai_payload(df, max_products=2)
        records = json.loads(payload)
        assert len(records) == 2
        for key in ["SkuId", "毛利率", "月销量"]:
            assert key in records[0]

    def test_prepare_payload_respects_max(self):
        from ai_analyzer import prepare_ai_payload

        df = pd.DataFrame([_make_row(i) for i in range(10)])
        payload = prepare_ai_payload(df, max_products=3)
        records = json.loads(payload)
        assert len(records) == 3

    def test_generate_report_rejects_placeholder_key(self):
        from ai_analyzer import generate_report

        cfg = {
            "ai": {
                "api_key": "sk-YOUR-DEEPSEEK-KEY",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
            }
        }
        result = generate_report(cfg, "[]")
        assert "error" in result
        assert "API Key" in result["error"]

    def test_generate_report_rejects_empty_key(self):
        from ai_analyzer import generate_report

        cfg = {"ai": {"api_key": "", "base_url": "https://api.deepseek.com"}}
        result = generate_report(cfg, "[]")
        assert "error" in result


class TestDashboardApi:
    def test_filter_and_score_returns_all_tab_cols(self):
        from main import filter_and_score, TAB_BASIC_COLS, TAB_COST_COLS

        df = pd.DataFrame([_make_row(1001), _make_row(1002)])
        cfg = {
            "filters": {
                "min_monthly_sales": 1,
                "min_gross_margin": 10.0,
                "max_competitors": 50,
                "max_purchase_price": 9999.0,
                "min_selling_price": 1.0,
                "categories": [],
            },
            "scoring": {
                "gross_margin": 0.3,
                "monthly_sales": 0.25,
                "conversion_rate": 0.25,
                "low_competition": 0.2,
            },
            "output": {"top_n": 20},
        }
        result_df, stats = filter_and_score(cfg, df)
        for col in ["SkuId", "品牌", "毛利率", "利润(预)", "综合评分", "data_source"]:
            assert col in result_df.columns, f"Missing column: {col}"

    def test_stats_keys(self):
        from main import filter_and_score

        df = pd.DataFrame([_make_row(1001)])
        cfg = {
            "filters": {
                "min_monthly_sales": 1,
                "min_gross_margin": 10.0,
                "max_competitors": 50,
                "max_purchase_price": 9999.0,
                "min_selling_price": 1.0,
                "categories": [],
            },
            "scoring": {
                "gross_margin": 0.3,
                "monthly_sales": 0.25,
                "conversion_rate": 0.25,
                "low_competition": 0.2,
            },
            "output": {"top_n": 20},
        }
        _, stats = filter_and_score(cfg, df)
        for key in [
            "total_before",
            "total_after",
            "shown",
            "avg_margin",
            "avg_sales",
            "avg_score",
        ]:
            assert key in stats, f"Missing stat: {key}"

    def test_only_scraped_rows_are_scored(self):
        from main import filter_and_score

        df = pd.DataFrame(
            [
                _make_row(1001, data_source="excel", margin=90.0, sales=99),
                _make_row(1002, data_source="scraped", margin=20.0, sales=3),
            ]
        )
        cfg = {
            "filters": {
                "min_monthly_sales": 0,
                "min_gross_margin": 0.0,
                "max_competitors": 99,
                "max_purchase_price": 9999.0,
                "min_selling_price": 0.0,
                "categories": [],
            },
            "scoring": {
                "gross_margin": 0.3,
                "monthly_sales": 0.25,
                "conversion_rate": 0.25,
                "low_competition": 0.2,
            },
            "output": {"top_n": 20},
        }
        out, _ = filter_and_score(cfg, df)
        assert out["SkuId"].tolist() == [1002]
        assert set(out["data_source"].tolist()) == {"scraped"}

    def test_zero_value_not_overwritten_by_est(self):
        from main import filter_and_score

        row = _make_row(1003, margin=0.0, sales=0, data_source="scraped")
        row["展示至下单转化率"] = 0.0
        row["跟卖数量"] = 0.0
        row["毛利率_est"] = 80.0
        row["月销量_est"] = 99
        row["展示至下单转化率_est"] = 9.9
        row["跟卖数量_est"] = 88
        df = pd.DataFrame([row])
        cfg = {
            "filters": {
                "min_monthly_sales": 0,
                "min_gross_margin": 0.0,
                "max_competitors": 99,
                "max_purchase_price": 9999.0,
                "min_selling_price": 0.0,
                "categories": [],
            },
            "scoring": {
                "gross_margin": 0.3,
                "monthly_sales": 0.25,
                "conversion_rate": 0.25,
                "low_competition": 0.2,
            },
            "output": {"top_n": 20},
        }
        out, _ = filter_and_score(cfg, df)
        assert len(out) == 1
        assert out.iloc[0]["SkuId"] == 1003


class TestTabColumns:
    def test_all_data_cols_deduplicated(self):
        from main import ALL_DATA_COLS, TAB_BASIC_COLS, TAB_TRAFFIC_COLS, TAB_COST_COLS

        assert len(ALL_DATA_COLS) == len(set(ALL_DATA_COLS))
        for col in TAB_BASIC_COLS + TAB_TRAFFIC_COLS + TAB_COST_COLS:
            assert col in ALL_DATA_COLS

    def test_three_tab_groups_defined(self):
        from main import TAB_BASIC_COLS, TAB_TRAFFIC_COLS, TAB_COST_COLS

        assert len(TAB_BASIC_COLS) >= 5
        assert len(TAB_TRAFFIC_COLS) >= 5
        assert len(TAB_COST_COLS) >= 5
        assert "SkuId" in TAB_BASIC_COLS
        assert "SkuId" in TAB_TRAFFIC_COLS
        assert "SkuId" in TAB_COST_COLS


class TestEstimator:
    def test_apply_estimates_adds_est_columns(self):
        from estimator import apply_estimates

        df = pd.DataFrame(
            [_make_row(2001, data_source="scraped", margin=None, sales=1)]
        )
        cfg = {
            "estimator": {
                "enabled": True,
                "version": "v1",
                "commission_rate_default": 0.14,
                "ad_rate_default": 0.05,
                "return_loss_rate": 0.03,
            }
        }
        out = apply_estimates(df, cfg)
        for col in [
            "毛利率_est",
            "展示至下单转化率_est",
            "estimate_confidence",
            "estimate_version",
        ]:
            assert col in out.columns
