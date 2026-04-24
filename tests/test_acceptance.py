"""Acceptance criteria tests — directly validates each hard acceptance criterion in the plan.

AC1: 1688命中样本中，阿里巴巴采购价(预) 非空率 >= 60%
AC2: 1688命中样本中，自定义1688运费金额 非空率 >= 50%
AC3: scraped数据中，至少2个评分关键字段(真实或_est)非空率 >= 40%
AC4: TopN中 scraped 占比为 100%（excel 不参与该验收）
AC5: scraped总数不足N时，TopN按scraped实际数量计
AC6: mapping_total 分母 = 有 mapping 的 SKU 数
AC7: 含0的字段补位前后仍为0，不触发 _est 覆盖
AC8: 不破坏现有接口行为（由其他测试覆盖，此处 smoke check）
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --------------- helpers ---------------


def _make_scraped_row(sku_id: int, price: float = 200.0, rating: float = 4.0,
                      comp: float = 5.0) -> dict:
    return {
        "SkuId": sku_id,
        "评级": rating,
        "绿标价": price,
        "黑标价": price * 1.1,
        "品牌": None,
        "一级类目": "电子产品",
        "三级类目": "手机壳",
        "店铺名称": "TestShop",
        "月销量": 10,
        "月销售额": price * 10,
        "展示至下单转化率": None,
        "商品卡加购率": None,
        "搜索至加入购物车转化率": None,
        "详情页访问量": None,
        "曝光次数": None,
        "销售动态占比": None,
        "跟卖数量": comp,
        "阿里巴巴采购价(预)": None,
        "自定义1688运费金额": None,
        "ozon物流费(预)": None,
        "平台佣金(预)": None,
        "佣金比例%": None,
        "总成本": None,
        "成本占比": None,
        "毛利率": None,
        "利润(预)": None,
        "商品链接": f"https://www.ozon.ru/product/test-{sku_id}/",
        "data_quality_flags": {},
        "data_source": "scraped",
    }


def _write_mapping_csv(tmp_path, rows: list[dict]) -> str:
    """Write a supplier_mapping.csv to tmp_path and return path."""
    path = tmp_path / "supplier_mapping.csv"
    header = "ozon_sku,supplier_keyword,supplier_url,purchase_price,freight_est,note\n"
    lines = [header]
    for r in rows:
        lines.append(
            f"{r['sku']},{r.get('keyword','')},{r.get('url','')},"
            f"{r.get('pp','')},{r.get('fe','')},{r.get('note','')}\n"
        )
    path.write_text("".join(lines), encoding="utf-8")
    return str(path)


# --------------- AC1 & AC2 ---------------


def test_ac1_purchase_price_fill_rate_ge_60pct(tmp_path):
    """AC1: In mapping-hit samples, 阿里巴巴采购价(预) non-null rate >= 60%."""
    from supplier_1688 import enrich_products_with_supplier

    # 10 mapping entries: 8 with purchase_price, 2 without
    mapping_rows = [
        {"sku": 1001, "keyword": "手机壳", "pp": "12.5", "fe": "8.0"},
        {"sku": 1002, "keyword": "耳机 TWS", "pp": "28.0", "fe": "10.0"},
        {"sku": 1003, "keyword": "瑜伽垫", "pp": "35.0", "fe": "15.0"},
        {"sku": 1004, "keyword": "吸尘器", "pp": "89.0", "fe": "18.0"},
        {"sku": 1005, "keyword": "积木", "pp": "18.5", "fe": "12.0"},
        {"sku": 1006, "keyword": "灯具 LED", "pp": "22.0", "fe": "8.0"},
        {"sku": 1007, "keyword": "雨伞 折叠", "pp": "15.0", "fe": "9.0"},
        {"sku": 1008, "keyword": "猫抓柱", "pp": "45.0", "fe": "20.0"},
        {"sku": 1009, "keyword": "手机壳 透明", "pp": "", "fe": "8.0"},  # no purchase_price
        {"sku": 1010, "keyword": "键盘 机械", "pp": "", "fe": ""},      # no prices
    ]
    csv_path = _write_mapping_csv(tmp_path, mapping_rows)

    # Products with matching category3 → keyword match passes
    products = [
        {**_make_scraped_row(1001), "三级类目": "手机壳"},
        {**_make_scraped_row(1002), "三级类目": "耳机"},
        {**_make_scraped_row(1003), "三级类目": "瑜伽垫"},
        {**_make_scraped_row(1004), "三级类目": "吸尘器"},
        {**_make_scraped_row(1005), "三级类目": "积木"},
        {**_make_scraped_row(1006), "三级类目": "灯具"},
        {**_make_scraped_row(1007), "三级类目": "雨伞"},
        {**_make_scraped_row(1008), "三级类目": "猫抓柱"},
        {**_make_scraped_row(1009), "三级类目": "手机壳"},
        {**_make_scraped_row(1010), "三级类目": "键盘"},
    ]

    cfg = {
        "supplier1688": {
            "enabled": True,
            "mapping_file": csv_path,
            "mapping_priority": "keyword_first",
            "match_rule": "category_based",
            "default_values": {"global_freight": 8.0, "category_freight": {}},
        }
    }
    enriched, stats = enrich_products_with_supplier(products, cfg)

    # Filter to "ok" status (mapping hit + category matched)
    ok_rows = [r for r in enriched if r.get("supplier_status") == "ok"]
    assert len(ok_rows) >= 1, "At least one row should be 'ok'"

    filled = sum(1 for r in ok_rows if r.get("阿里巴巴采购价(预)") is not None)
    fill_rate = filled / len(ok_rows)
    assert fill_rate >= 0.60, (
        f"AC1 FAIL: 阿里巴巴采购价(预) fill rate {fill_rate:.0%} < 60% "
        f"(filled={filled}, ok_total={len(ok_rows)})"
    )


def test_ac2_freight_fill_rate_ge_50pct(tmp_path):
    """AC2: In mapping-hit samples, 自定义1688运费金额 non-null rate >= 50%."""
    from supplier_1688 import enrich_products_with_supplier

    # Some rows have freight_est, some don't (all fall back to global_freight=8.0)
    mapping_rows = [
        {"sku": 2001, "keyword": "手机壳", "pp": "10.0", "fe": "8.0"},
        {"sku": 2002, "keyword": "耳机", "pp": "20.0", "fe": ""},   # no CSV freight → uses global
        {"sku": 2003, "keyword": "瑜伽垫", "pp": "30.0", "fe": "15.0"},
        {"sku": 2004, "keyword": "积木", "pp": "", "fe": ""},
    ]
    csv_path = _write_mapping_csv(tmp_path, mapping_rows)

    products = [
        {**_make_scraped_row(2001), "三级类目": "手机壳"},
        {**_make_scraped_row(2002), "三级类目": "耳机"},
        {**_make_scraped_row(2003), "三级类目": "瑜伽垫"},
        {**_make_scraped_row(2004), "三级类目": "积木"},
    ]

    cfg = {
        "supplier1688": {
            "enabled": True,
            "mapping_file": csv_path,
            "mapping_priority": "keyword_first",
            "match_rule": "category_based",
            "default_values": {"global_freight": 8.0, "category_freight": {}},
        }
    }
    enriched, _ = enrich_products_with_supplier(products, cfg)

    ok_rows = [r for r in enriched if r.get("supplier_status") == "ok"]
    assert len(ok_rows) >= 1

    filled = sum(1 for r in ok_rows if r.get("自定义1688运费金额") is not None)
    fill_rate = filled / len(ok_rows)
    assert fill_rate >= 0.50, (
        f"AC2 FAIL: 自定义1688运费金额 fill rate {fill_rate:.0%} < 50%"
    )


# --------------- AC3 ---------------


def test_ac3_two_key_fields_fill_rate_ge_40pct():
    """AC3: scraped data: at least 2 key scoring fields (real or _est) non-null rate >= 40%."""
    from estimator import apply_estimates

    # 10 scraped rows, all with 绿标价 → estimator can produce 毛利率_est and 展示至下单转化率_est
    rows = [_make_scraped_row(sku, price=200.0 + sku) for sku in range(3001, 3011)]
    df = pd.DataFrame(rows)

    cfg = {
        "estimator": {
            "enabled": True,
            "version": "v1",
            "commission_rate_default": 0.14,
            "ad_rate_default": 0.05,
            "return_loss_rate": 0.03,
            "conversion_weights": {
                "rating": 0.5, "reviews": 0.2,
                "price_competitiveness": 0.2, "competition": 0.1,
            },
            "confidence_rules": {"required_fields": ["绿标价", "评级", "跟卖数量"]},
        }
    }
    out = apply_estimates(df, cfg)

    total = len(out)
    for key_col in ["毛利率_est", "展示至下单转化率_est"]:
        filled = out[key_col].notna().sum()
        rate = filled / total
        assert rate >= 0.40, (
            f"AC3 FAIL: {key_col} fill rate {rate:.0%} < 40%"
        )


# --------------- AC4 & AC5 ---------------


def test_ac4_topn_scraped_only():
    """AC4: filter_and_score returns ONLY scraped rows (excel excluded)."""
    from main import filter_and_score

    rows = [
        {**_make_scraped_row(4001), "data_source": "excel", "毛利率": 90.0},
        {**_make_scraped_row(4002), "data_source": "scraped", "毛利率": 20.0},
        {**_make_scraped_row(4003), "data_source": "scraped", "毛利率": 55.0},
    ]
    df = pd.DataFrame(rows)
    cfg = {
        "filters": {
            "min_monthly_sales": 0, "min_gross_margin": 0.0,
            "max_competitors": 99, "max_purchase_price": 9999.0,
            "min_selling_price": 0.0, "categories": [],
        },
        "scoring": {
            "gross_margin": 0.3, "monthly_sales": 0.25,
            "conversion_rate": 0.25, "low_competition": 0.2,
        },
        "output": {"top_n": 20},
    }
    out, _ = filter_and_score(cfg, df)
    assert set(out["data_source"].unique()) == {"scraped"}, (
        "AC4 FAIL: non-scraped rows in TopN result"
    )
    assert 4001 not in out["SkuId"].tolist(), "AC4 FAIL: excel row included in TopN"


def test_ac5_topn_capped_by_scraped_count():
    """AC5: When scraped count < top_n, result length == scraped count."""
    from main import filter_and_score

    # Only 3 scraped rows, top_n=20
    rows = [_make_scraped_row(5001), _make_scraped_row(5002), _make_scraped_row(5003)]
    df = pd.DataFrame(rows)
    cfg = {
        "filters": {
            "min_monthly_sales": 0, "min_gross_margin": 0.0,
            "max_competitors": 99, "max_purchase_price": 9999.0,
            "min_selling_price": 0.0, "categories": [],
        },
        "scoring": {
            "gross_margin": 0.3, "monthly_sales": 0.25,
            "conversion_rate": 0.25, "low_competition": 0.2,
        },
        "output": {"top_n": 20},
    }
    out, _ = filter_and_score(cfg, df)
    assert len(out) == 3, f"AC5 FAIL: expected 3 rows, got {len(out)}"


# --------------- AC6 ---------------


def test_ac6_mapping_total_denominator(tmp_path):
    """AC6: mapping_total == SKU count WITH mapping (not all products)."""
    from supplier_1688 import enrich_products_with_supplier

    # 3 in mapping, 2 not in mapping
    mapping_rows = [
        {"sku": 6001, "keyword": "手机壳", "pp": "10.0", "fe": "8.0"},
        {"sku": 6002, "keyword": "耳机", "pp": "20.0", "fe": ""},
        {"sku": 6003, "keyword": "瑜伽垫", "pp": "30.0", "fe": "15.0"},
    ]
    csv_path = _write_mapping_csv(tmp_path, mapping_rows)

    products = [
        {**_make_scraped_row(6001), "三级类目": "手机壳"},
        {**_make_scraped_row(6002), "三级类目": "耳机"},
        {**_make_scraped_row(6003), "三级类目": "瑜伽垫"},
        {**_make_scraped_row(9991)},  # NOT in mapping
        {**_make_scraped_row(9992)},  # NOT in mapping
    ]

    cfg = {
        "supplier1688": {
            "enabled": True,
            "mapping_file": csv_path,
            "mapping_priority": "keyword_first",
            "match_rule": "category_based",
            "default_values": {"global_freight": 8.0, "category_freight": {}},
        }
    }
    _, stats = enrich_products_with_supplier(products, cfg)

    assert stats["mapping_total"] == 3, (
        f"AC6 FAIL: mapping_total={stats['mapping_total']}, expected 3 "
        f"(only SKUs WITH a mapping entry count)"
    )


# --------------- AC7 ---------------


def test_ac7_zero_value_not_overwritten_by_est():
    """AC7: Fields with value 0 are not overwritten by _est fallback."""
    from main import _with_est_fallback

    import numpy as np

    df = pd.DataFrame([{
        "SkuId": 7001,
        "毛利率": 0.0,          # real value is zero — must NOT be replaced
        "毛利率_est": 80.0,
        "展示至下单转化率": 0.0,
        "展示至下单转化率_est": 9.9,
        "月销量": 0,
        "月销量_est": 99,
    }])

    margin = _with_est_fallback(df, "毛利率")
    assert margin.iloc[0] == 0.0, (
        f"AC7 FAIL: 毛利率=0 was replaced by _est={df['毛利率_est'].iloc[0]}"
    )

    conv = _with_est_fallback(df, "展示至下单转化率")
    assert conv.iloc[0] == 0.0, (
        f"AC7 FAIL: 展示至下单转化率=0 was replaced by _est"
    )
