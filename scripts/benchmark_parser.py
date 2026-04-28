#!/usr/bin/env python3
"""Benchmark parser accuracy against ground truth fixtures.

Loads HTML fixture files from tests/fixtures/, runs parse_search_page() on each,
and compares extracted fields against ground truth JSON files.

Output format: prints a single JSON object as the last line of stdout.
"""

import json
import os
import sys
from pathlib import Path

# Allow importing from repo root without installing the package
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from parser import parse_search_page

FIXTURES_DIR = BASE_DIR / "tests" / "fixtures"

# The 6 key fields defined in the improvement goal
KEY_FIELDS = ["sku_id", "title", "price_current", "price_original", "rating", "seller"]

# Mapping from parser output keys to benchmark field names
PARSER_TO_BENCHMARK = {
    "sku_id": "sku_id",
    "title": "title",
    "price": "price_current",
    "original_price": "price_original",
    "rating": "rating",
    "seller": "seller",
}


def load_fixtures() -> list[dict]:
    """Discover and load all fixture HTML files and their ground truth JSON.

    Returns a list of dicts with keys: name, html, truth (list of expected cards).
    """
    fixtures = []
    html_files = sorted(FIXTURES_DIR.glob("fixture_*.html"))
    if not html_files:
        return fixtures

    for html_file in html_files:
        truth_file = html_file.with_name(html_file.stem + "_truth.json")
        if not truth_file.exists():
            print(
                f"Warning: No ground truth file for {html_file.name}", file=sys.stderr
            )
            continue

        with open(html_file, "r", encoding="utf-8") as f:
            html = f.read()
        with open(truth_file, "r", encoding="utf-8") as f:
            truth = json.load(f)

        if not isinstance(truth, list):
            print(
                f"Warning: {truth_file.name} must contain a JSON array, skipping",
                file=sys.stderr,
            )
            continue

        fixtures.append({"name": html_file.stem, "html": html, "truth": truth})

    return fixtures


def _values_match(parsed_val, truth_val) -> bool:
    """Check if a parsed value matches ground truth.

    - None == None is a match (both missing)
    - Numeric values compared with float tolerance
    - Strings compared case-insensitive after stripping
    """
    if parsed_val is None and truth_val is None:
        return True
    if parsed_val is None or truth_val is None:
        return False

    # Numeric comparison with tolerance
    if isinstance(parsed_val, (int, float)) and isinstance(truth_val, (int, float)):
        return abs(parsed_val - truth_val) <= 0.05

    # String comparison (case-insensitive, stripped)
    return str(parsed_val).strip().lower() == str(truth_val).strip().lower()


def compare_card(parsed_card: dict, truth_card: dict) -> dict[str, bool]:
    """Compare a single parsed product card against ground truth.

    Returns a dict of field_name -> bool indicating whether each field matched.
    """
    results = {}
    for bench_key in KEY_FIELDS:
        # Determine which parser key maps to this benchmark key
        parser_key = None
        for pk, bk in PARSER_TO_BENCHMARK.items():
            if bk == bench_key:
                parser_key = pk
                break

        parsed_val = parsed_card.get(parser_key) if parser_key else None
        truth_val = truth_card.get(bench_key)
        results[bench_key] = _values_match(parsed_val, truth_val)

    return results


def run_benchmark() -> dict:
    """Execute the benchmark and return results as a dict."""
    fixtures = load_fixtures()

    if not fixtures:
        print(
            "No fixture files found in tests/fixtures/. "
            "Run scripts/collect_fixtures.py to collect real Ozon HTML fixtures, "
            "or ensure fixture_*.html and fixture_*_truth.json files exist.",
            file=sys.stderr,
        )
        return {
            "primary": 0.0,
            "sub_scores": {
                "sku_id": 0.0,
                "title": 0.0,
                "price_current": 0.0,
                "price_original": 0.0,
                "rating": 0.0,
                "seller": 0.0,
            },
            "total_cards": 0,
            "total_fields": 0,
            "matched_fields": 0,
        }

    total_cards = 0
    total_fields = 0
    matched_fields = 0

    # Per-field accumulators
    field_totals = {k: 0 for k in KEY_FIELDS}
    field_matched = {k: 0 for k in KEY_FIELDS}

    for fixture in fixtures:
        cards = parse_search_page(fixture["html"])
        truth_list = fixture["truth"]

        # Index both parsed and truth by sku_id for matching
        parsed_by_sku: dict[str, dict] = {}
        for c in cards:
            sid = str(c.get("sku_id") or "")
            if sid:
                parsed_by_sku[sid] = c

        truth_by_sku: dict[str, dict] = {}
        for t in truth_list:
            sid = str(t.get("sku_id") or "")
            if sid:
                truth_by_sku[sid] = t

        # Compare each truth card against its parsed counterpart
        for truth_sku, truth_card in truth_by_sku.items():
            total_cards += 1
            parsed_card = parsed_by_sku.get(truth_sku)

            if parsed_card is not None:
                comparisons = compare_card(parsed_card, truth_card)
            else:
                # Parser failed to find this card at all
                comparisons = {k: False for k in KEY_FIELDS}

            for field, is_match in comparisons.items():
                total_fields += 1
                field_totals[field] += 1
                if is_match:
                    matched_fields += 1
                    field_matched[field] += 1

    # Calculate overall success rate
    primary = round((matched_fields / total_fields) * 100, 1) if total_fields > 0 else 0.0

    # Calculate per-field success rates
    sub_scores = {}
    for field in KEY_FIELDS:
        if field_totals[field] == 0:
            sub_scores[field] = 0.0
        else:
            sub_scores[field] = round(
                (field_matched[field] / field_totals[field]) * 100, 1
            )

    return {
        "primary": primary,
        "sub_scores": sub_scores,
        "total_cards": total_cards,
        "total_fields": total_fields,
        "matched_fields": matched_fields,
    }


if __name__ == "__main__":
    result = run_benchmark()
    # The last line of stdout is the JSON result (required by self-improve harness)
    print(json.dumps(result))
