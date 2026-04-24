import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_load_mapping_reads_keyword_first(tmp_path):
    from supplier_1688 import load_mapping

    p = tmp_path / "supplier_mapping.csv"
    p.write_text(
        "ozon_sku,supplier_keyword,supplier_url,note\n123,手机壳,,demo\n",
        encoding="utf-8",
    )
    mapping = load_mapping(str(p))
    assert 123 in mapping
    assert mapping[123]["supplier_keyword"] == "手机壳"


def test_enrich_marks_mapping_miss_when_keyword_empty(tmp_path):
    from supplier_1688 import enrich_products_with_supplier

    p = tmp_path / "supplier_mapping.csv"
    p.write_text(
        "ozon_sku,supplier_keyword,supplier_url,note\n"
        "123,,https://detail.1688.com/offer/x.html,demo\n",
        encoding="utf-8",
    )
    cfg = {
        "supplier1688": {
            "enabled": True,
            "mapping_file": str(p),
            "default_values": {"global_freight": 8.0, "category_freight": {}},
        }
    }
    rows, stats = enrich_products_with_supplier(
        [
            {
                "SkuId": 123,
                "三级类目": "手机壳",
                "一级类目": "电子产品",
                "data_source": "scraped",
            }
        ],
        cfg,
    )
    assert rows[0]["supplier_status"] == "mapping_miss"
    assert stats["mapping_miss"] == 1


def test_merge_supplier_fields_backfills_dataframe():
    import pandas as pd
    from supplier_1688 import merge_supplier_fields

    base_df = pd.DataFrame(
        [{"SkuId": 123, "阿里巴巴采购价(预)": None, "自定义1688运费金额": None}]
    )
    supplier_df = pd.DataFrame(
        [
            {
                "SkuId": 123,
                "阿里巴巴采购价(预)": 12.5,
                "自定义1688运费金额": 8.0,
                "supplier_status": "ok",
            }
        ]
    )
    out = merge_supplier_fields(base_df, supplier_df)
    assert out.iloc[0]["阿里巴巴采购价(预)"] == 12.5
    assert out.iloc[0]["自定义1688运费金额"] == 8.0
