"""TDD: Parser unit tests — written BEFORE implementation."""

from parser import (
    parse_product_card,
    parse_search_page,
    normalize_product,
    is_missing_value,
    first_non_null,
    build_data_quality_flags,
)


class TestParseProductCard:
    def test_valid_card_extracts_all_fields(self, sample_product_card_html):
        result = parse_product_card(sample_product_card_html)
        assert result["sku_id"] == "3340449279"
        assert "Foundation Liquid Concealer" in result["title"]
        assert result["price"] == 162.0
        # Fixture has no <s>, <del>, or strikethrough class → no original_price
        assert result["original_price"] is None
        assert result["rating"] == 3.7
        assert result["reviews"] == 42
        assert result["seller"] == "NorthVoyage"
        assert "/product/" in result["url"]
        assert "3340449279" in result["url"]

    def test_extracts_original_price_with_strikethrough(self):
        html = """
        <div class="tile-root">
          <a href="/product/test-12345/"><span>Test Title Here OK</span></a>
          <span class="tsHeadline500Medium">100 ₽</span>
          <span class="tsBodyControl400Small" style="text-decoration:line-through">150 ₽</span>
          <span>4.5 99 </span>
        </div>
        """
        result = parse_product_card(html)
        assert result["price"] == 100.0
        assert result["original_price"] == 150.0

    def test_missing_fields_returns_none(self, sample_product_card_missing_fields):
        result = parse_product_card(sample_product_card_missing_fields)
        assert result["sku_id"] == "9999999999"
        assert result["price"] is None
        assert result["rating"] is None
        assert result["reviews"] is None

    def test_returns_dict(self, sample_product_card_html):
        result = parse_product_card(sample_product_card_html)
        assert isinstance(result, dict)
        assert "sku_id" in result


class TestParseSearchPage:
    def test_extracts_multiple_cards(self, sample_search_page_html):
        cards = parse_search_page(sample_search_page_html)
        assert len(cards) == 3
        assert all(isinstance(c, dict) for c in cards)

    def test_card_skus_correct(self, sample_search_page_html):
        cards = parse_search_page(sample_search_page_html)
        skus = [c["sku_id"] for c in cards]
        assert "1111111111" in skus
        assert "2222222222" in skus
        assert "3333333333" in skus

    def test_empty_page_returns_empty_list(self):
        empty_html = "<html><body><div class='search-page'></div></body></html>"
        cards = parse_search_page(empty_html)
        assert cards == []


class TestNormalizeProduct:
    def test_maps_to_app_schema(self):
        raw = {
            "sku_id": "1234567890",
            "title": "Test Product",
            "price": 150.0,
            "original_price": 180.0,
            "rating": 4.2,
            "reviews": 30,
            "seller": "TestShop",
            "url": "/product/test-product-1234567890/",
        }
        result = normalize_product(raw)
        assert result["SkuId"] == 1234567890
        assert result["绿标价"] == 150.0
        assert result["黑标价"] == 180.0
        assert result["评级"] == 4.2
        assert (
            result["商品链接"] == "https://www.ozon.ru/product/test-product-1234567890/"
        )
        assert result["data_source"] == "scraped"

    def test_missing_fields_produce_nan(self):
        raw = {
            "sku_id": "9999999999",
            "title": "Sparse Product",
            "price": None,
            "original_price": None,
            "rating": None,
            "reviews": None,
            "seller": None,
            "url": "/product/sparse-product-9999999999/",
        }
        result = normalize_product(raw)
        assert result["SkuId"] == 9999999999
        assert result["绿标价"] is None
        assert result["毛利率"] is None
        assert result["利润(预)"] is None
        assert result["阿里巴巴采购价(预)"] is None
        assert result["展示至下单转化率"] is None
        assert result["data_source"] == "scraped"


class TestMissingValue:
    def test_zero_is_not_missing(self):
        assert is_missing_value(0) is False
        assert is_missing_value(0.0) is False
        assert is_missing_value("0") is False

    def test_empty_variants_are_missing(self):
        assert is_missing_value(None) is True
        assert is_missing_value("") is True


class TestBackfillRules:
    def test_first_non_null_keeps_zero(self):
        assert first_non_null(None, "", 0, 10) == 0

    def test_normalize_prefers_detail_fields(self):
        raw = {
            "sku_id": "10001",
            "url": "/product/demo-10001/",
            "detail_category_level1": "电子产品",
            "list_category_level1": "家电",
            "detail_category_level3": "手机壳",
            "list_category_level3": "耳机",
            "detail_competitor_count": 2,
            "list_competitor_count": 9,
            "detail_monthly_sales": "月销 123",
        }
        result = normalize_product(raw)
        assert result["一级类目"] == "电子产品"
        assert result["三级类目"] == "手机壳"
        assert result["跟卖数量"] == 2
        assert result["月销量"] == 123

    def test_build_data_quality_flags(self):
        flags = build_data_quality_flags(
            {
                "detail_category_level1": "电子产品",
                "detail_category_level3": "手机壳",
                "detail_competitor_count": 5,
                "detail_monthly_sales": "月销 88",
            }
        )
        assert flags["has_category"] is True
        assert flags["has_competition"] is True
        assert flags["has_monthly_sales"] is True
