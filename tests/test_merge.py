"""TDD: Merge + filter integration tests — verifies scraped rows survive the pipeline."""

import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_excel_row(sku_id, margin=50.0, sales=5, competitors=10, price=150.0, purchase=20.0, conv=0.5):
    """Helper to create an Excel-like row with all columns."""
    return {
        "SkuId": sku_id, "评级": 4.0, "黑标价": price * 1.1, "绿标价": price,
        "品牌": "TestBrand", "一级类目": "测试类目", "三级类目": "测试子类目",
        "店铺Id": 1000, "店铺名称": "TestShop", "月销量": sales, "月销售额": sales * price,
        "销售动态占比": 100.0, "展示至下单转化率": conv, "商品卡加购率": 3.0,
        "推广费用占比": 0.0, "商品卡创建时间": "2026-01-01", "搜索至加入购物车转化率": 0.5,
        "详情页访问量": 200, "曝光次数": 1000, "配送方式": "fbs", "重量（克）": 500,
        "长*宽*高(mm)": "100*100*100mm", "跟卖数量": competitors,
        "最低跟卖价(绿标价)": price * 0.8, "最低跟卖价(黑标价)": 0,
        "阿里巴巴采购价(预)": purchase, "自定义1688运费金额": 2.0,
        "ozon物流费(预)": 20.0, "快递通道": "Small", "平台佣金(预)": price * 0.14,
        "佣金比例%": "14.0%", "利润(预)": price * margin / 100,
        "毛利率": margin, "总成本": price * (1 - margin / 100),
        "成本占比": 1 - margin / 100, "AI智能估算价格": price * 1.2,
        "商品链接": f"https://www.ozon.ru/product/{sku_id}",
        "data_source": "excel",
    }


def _make_scraped_row(sku_id, price=200.0, rating=4.5):
    """Helper to create a scraped row — missing proprietary columns."""
    return {
        "SkuId": sku_id, "评级": rating, "黑标价": price * 1.1, "绿标价": price,
        "品牌": None, "一级类目": None, "三级类目": None,
        "店铺Id": None, "店铺名称": None, "月销量": None, "月销售额": None,
        "销售动态占比": None, "展示至下单转化率": None, "商品卡加购率": None,
        "推广费用占比": None, "商品卡创建时间": None, "搜索至加入购物车转化率": None,
        "详情页访问量": None, "曝光次数": None, "配送方式": None, "重量（克）": None,
        "长*宽*高(mm)": None, "跟卖数量": None,
        "最低跟卖价(绿标价)": None, "最低跟卖价(黑标价)": None,
        "阿里巴巴采购价(预)": None, "自定义1688运费金额": None,
        "ozon物流费(预)": None, "快递通道": None, "平台佣金(预)": None,
        "佣金比例%": None, "利润(预)": None,
        "毛利率": None, "总成本": None,
        "成本占比": None, "AI智能估算价格": None,
        "商品链接": f"https://www.ozon.ru/product/{sku_id}",
        "data_source": "scraped",
    }


class TestMergeDeduplication:
    def test_no_duplicate_skus_after_merge(self):
        """Merging scraped data with overlapping SkuIds keeps only one copy."""
        from main import merge_dataframes

        existing = pd.DataFrame([_make_excel_row(1001), _make_excel_row(1002)])
        scraped = pd.DataFrame([_make_scraped_row(1002), _make_scraped_row(1003)])

        merged = merge_dataframes(existing, scraped)
        assert len(merged) == 3  # 1001 + 1002 (excel wins) + 1003
        assert merged["SkuId"].nunique() == 3
        # Excel row should be preferred over scraped for duplicate SkuId
        row_1002 = merged[merged["SkuId"] == 1002].iloc[0]
        assert row_1002["data_source"] == "excel"

    def test_scraped_only_rows_preserved(self):
        """Scraped rows with unique SkuIds are kept."""
        from main import merge_dataframes

        existing = pd.DataFrame([_make_excel_row(1001)])
        scraped = pd.DataFrame([_make_scraped_row(2001), _make_scraped_row(2002)])

        merged = merge_dataframes(existing, scraped)
        assert len(merged) == 3
        scraped_rows = merged[merged["data_source"] == "scraped"]
        assert len(scraped_rows) == 2


class TestFilterWithMissingColumns:
    def test_scraped_rows_survive_nan_filters(self):
        """Scraped rows with NaN in filter columns should NOT be silently dropped."""
        from main import filter_and_score

        rows = [_make_excel_row(1001), _make_excel_row(1002), _make_scraped_row(2001)]
        df = pd.DataFrame(rows)

        cfg = {
            "filters": {
                "min_monthly_sales": 2,
                "min_gross_margin": 30.0,
                "max_competitors": 20,
                "max_purchase_price": 200.0,
                "min_selling_price": 50.0,
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
        # The scraped row (sku 2001) must appear — not filtered out by NaN
        skus = result_df["SkuId"].tolist()
        assert 2001 in skus, "Scraped row was silently dropped by NaN filter!"

    def test_excel_rows_unaffected_by_nan_guards(self):
        """Adding NaN guards must not change behavior for complete Excel rows."""
        from main import filter_and_score

        rows = [_make_excel_row(1001, margin=20.0)]  # margin below threshold
        df = pd.DataFrame(rows)

        cfg = {
            "filters": {
                "min_monthly_sales": 2,
                "min_gross_margin": 30.0,
                "max_competitors": 20,
                "max_purchase_price": 200.0,
                "min_selling_price": 50.0,
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
        # Row with margin=20 should be filtered out (below 30 threshold)
        assert 1001 not in result_df["SkuId"].tolist()
