"""Pure HTML parsing functions for Ozon product pages. No browser dependency."""

import re
import math
import html as html_lib
from html.parser import HTMLParser


def _extract_number(text):
    """Extract first number from text like '29 690 ₽' -> 29690.0, '42 отзыва' -> 42."""
    if not text:
        return None
    # Remove all whitespace variants (regular space, thin space, non-breaking space)
    cleaned = text.replace("\u2009", "").replace("\xa0", "").replace(" ", "")
    m = re.search(r"[\d]+(?:[.,]\d+)?", cleaned)
    if m:
        return float(m.group().replace(",", "."))
    return None


_PRICE_RE = r"([\d\s\u2009\xa0]+)\s*₽"
_ORIGINAL_PRICE_MARKER_RE = re.compile(
    r"(?:price[_-]?original|original[_-]?price|old[_-]?price|"
    r"oldPrice|originalPrice|strikethrough|line-through|textOriginalprice)",
    re.IGNORECASE,
)


def _extract_price_from_fragment(fragment: str):
    m = re.search(_PRICE_RE, fragment)
    if not m:
        return None
    num = _extract_number(m.group(1))
    return num if num and num > 1 else None


def _extract_original_price(card_html: str):
    # Check for <s> or <del> tags containing a price
    for m in re.finditer(r"(?is)<(?:s|del)\b[^>]*>.*?</(?:s|del)>", card_html):
        price = _extract_price_from_fragment(m.group(0))
        if price:
            return price

    # For each ₽ price found, check surrounding context for strikethrough markers
    for pm in re.finditer(_PRICE_RE, card_html):
        # Look at 200 chars before this price for strikethrough/line-through signals
        ctx_start = max(0, pm.start() - 200)
        ctx = card_html[ctx_start:pm.end()]
        if _ORIGINAL_PRICE_MARKER_RE.search(ctx) or re.search(
            r"(?i)text-decoration\s*:\s*line-through|line-through", ctx
        ):
            price = _extract_price_from_fragment(pm.group(0))
            if price:
                return price

    return None


def _extract_rating_and_reviews(card_html: str):
    # data-rating / data-value attributes (future-proof, may appear in later layouts)
    m = re.search(r"""(?is)\bdata-rating=["']([1-5](?:[.,]\d{1,4})?)["']""", card_html)
    if m:
        return float(m.group(1).replace(",", ".")), None

    m = re.search(
        r"""(?is)<[^>]*(?:rating|star|review|comment)[^>]*\bdata-value=["']([1-5](?:[.,]\d{1,4})?)["'][^>]*>""",
        card_html,
    )
    if m:
        return float(m.group(1).replace(",", ".")), None

    for label in re.findall(r"""(?is)\baria-label=["']([^"']+)["']""", card_html):
        if re.search(r"(?i)rating|рейтинг|зв[её]зд", label):
            m = re.search(r"([1-5](?:[.,]\d{1,4})?)", label)
            if m:
                return float(m.group(1).replace(",", ".")), None

    # Category page HTML: rating and review count are in separate spans
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", card_html))
    text = re.sub(r"[\s\u2009\xa0]+", " ", text).strip()

    m = re.search(
        r"(?<!\d)([1-5][.,]\d)\s+(\d[\d\s\u2009\xa0]*)\s*"
        r"(?:отзыв(?:ов|а)?|оцен(?:ок|ки)?|review|reviews)?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None, None

    rating = float(m.group(1).replace(",", "."))
    reviews_raw = re.sub(r"\D", "", m.group(2))
    reviews = int(reviews_raw) if reviews_raw else None
    return rating, reviews


def parse_product_card(html: str) -> dict:
    """Extract product data from a single product card's HTML.

    Returns dict with keys: sku_id, title, price, original_price, rating, reviews, seller, url
    """
    # --- URL & SKU ---
    url = None
    sku_id = None
    url_match = re.search(r'href="(/product/[^"]+)"', html)
    if url_match:
        url = url_match.group(1)
        # SKU is the trailing digits in the URL slug: /product/name-DIGITS/
        sku_match = re.search(r"-(\d{5,})/?", url)
        if sku_match:
            sku_id = sku_match.group(1)

    # Fallback: data-sku attribute (older Ozon layout)
    if not sku_id:
        sku_attr = re.search(r'data-sku="(\d+)"', html)
        if sku_attr:
            sku_id = sku_attr.group(1)

    # --- Title ---
    # Find span texts >15 chars that aren't prices, stock labels, or short noise
    title = None
    spans = re.findall(r"<span[^>]*>([^<]{15,})</span>", html)
    skip_patterns = re.compile(
        r"(₽|шт\s*осталось|баллов|бонус|^[\d\s\u2009.,]+$)", re.IGNORECASE
    )
    for candidate in spans:
        candidate = candidate.strip()
        if not skip_patterns.search(candidate):
            title = candidate
            break

    # --- Prices ---
    # Find all price patterns (digits + ₽), take first as current price
    price_patterns = re.findall(r"([\d\s\u2009\xa0]+)\s*₽", html)
    prices = []
    for p in price_patterns:
        num = _extract_number(p)
        if num and num > 1:
            prices.append(num)

    price = prices[0] if prices else None
    original_price = _extract_original_price(html)

    # --- Rating & Reviews ---
    rating, reviews = _extract_rating_and_reviews(html)
    # Validate rating range on the extracted result
    if rating is not None and not (1.0 <= rating <= 5.0):
        rating = None

    # --- Seller ---
    seller = None
    seller_attr = re.search(r'data-seller="([^"]*)"', html)
    if seller_attr:
        seller = seller_attr.group(1)

    return {
        "sku_id": sku_id,
        "title": title,
        "price": price,
        "original_price": original_price,
        "rating": rating,
        "reviews": reviews,
        "seller": seller,
        "url": url,
    }


def parse_search_page(page_html: str) -> list[dict]:
    """Extract all product cards from a full search/category page HTML.

    Returns list of dicts (each from parse_product_card).
    """
    cards = []

    # Find tile-root divs (Ozon's product card container)
    pattern = r'<div[^>]*class="[^"]*tile-root[^"]*"[^>]*>'
    matches = list(re.finditer(pattern, page_html))

    if not matches:
        return []

    for i, match in enumerate(matches):
        start = match.start()
        end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else min(start + 15000, len(page_html))
        )
        card_html = page_html[start:end]
        parsed = parse_product_card(card_html)
        if parsed.get("sku_id"):
            cards.append(parsed)

    return cards


def normalize_product(raw: dict) -> dict:
    """Map scraped field names to the app's column schema.

    Proprietary columns (毛利率, 利润, 采购价, etc.) are set to None
    since they cannot be obtained from Ozon public pages.
    """
    sku_id = raw.get("sku_id")
    try:
        sku_int = int(sku_id) if sku_id else None
    except (ValueError, TypeError):
        sku_int = None

    url = raw.get("url", "")
    if url and not url.startswith("http"):
        url = f"https://www.ozon.ru{url}"

    category1 = first_non_null(
        raw.get("detail_category_level1"),
        raw.get("一级类目"),
        raw.get("list_category_level1"),
    )
    category3 = first_non_null(
        raw.get("detail_category_level3"),
        raw.get("三级类目"),
        raw.get("list_category_level3"),
    )
    competitors = first_non_null(
        raw.get("detail_competitor_count"),
        raw.get("跟卖数量"),
        raw.get("list_competitor_count"),
    )
    monthly_sales = _parse_sales_hint(
        first_non_null(
            raw.get("detail_monthly_sales"),
            raw.get("月销量"),
            raw.get("list_monthly_sales"),
        )
    )
    quality_flags = build_data_quality_flags(raw)

    return {
        "SkuId": sku_int,
        "评级": raw.get("rating"),
        "黑标价": raw.get("original_price"),
        "绿标价": raw.get("price"),
        "品牌": None,
        "一级类目": category1,
        "三级类目": category3,
        "店铺Id": None,
        "店铺名称": raw.get("seller"),
        "月销量": monthly_sales,
        "月销售额": None,
        "销售动态占比": None,
        "展示至下单转化率": None,
        "商品卡加购率": None,
        "推广费用占比": None,
        "商品卡创建时间": None,
        "搜索至加入购物车转化率": None,
        "详情页访问量": None,
        "曝光次数": None,
        "配送方式": None,
        "重量（克）": None,
        "长*宽*高(mm)": None,
        "跟卖数量": competitors,
        "最低跟卖价(绿标价)": None,
        "最低跟卖价(黑标价)": None,
        "阿里巴巴采购价(预)": None,
        "自定义1688运费金额": None,
        "ozon物流费(预)": None,
        "快递通道": None,
        "平台佣金(预)": None,
        "佣金比例%": None,
        "利润(预)": None,
        "毛利率": None,
        "总成本": None,
        "成本占比": None,
        "AI智能估算价格": None,
        "商品链接": url,
        "data_quality_flags": quality_flags,
        "data_source": "scraped",
    }


def is_missing_value(value) -> bool:
    """Treat None/NaN/empty string as missing; keep numeric zero as valid."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def first_non_null(*vals):
    """Return first value that is not missing (0 is valid)."""
    for value in vals:
        if not is_missing_value(value):
            return value
    return None


def _parse_sales_hint(value):
    """Parse explicit sales text only (e.g. '月销 123')."""
    if is_missing_value(value):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    m = re.search(r"(?:月销|购买|sold|sales)[^\d]{0,6}(\d+)", text, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))


def build_data_quality_flags(raw):
    """Build light data quality flags for downstream logs."""
    monthly_sales = _parse_sales_hint(
        first_non_null(
            raw.get("detail_monthly_sales"),
            raw.get("月销量"),
            raw.get("list_monthly_sales"),
        )
    )
    has_category = (
        first_non_null(
            raw.get("detail_category_level1"),
            raw.get("一级类目"),
            raw.get("list_category_level1"),
        )
        is not None
        and first_non_null(
            raw.get("detail_category_level3"),
            raw.get("三级类目"),
            raw.get("list_category_level3"),
        )
        is not None
    )
    has_competition = (
        first_non_null(
            raw.get("detail_competitor_count"),
            raw.get("跟卖数量"),
            raw.get("list_competitor_count"),
        )
        is not None
    )
    flags = {
        "has_category": bool(has_category),
        "has_competition": bool(has_competition),
        "has_monthly_sales": monthly_sales is not None,
    }
    return flags
