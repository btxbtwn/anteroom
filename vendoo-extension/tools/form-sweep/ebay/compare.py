"""Compare eBay's Create listing page with Anteroom's eBay form, category by category.

Usage (Anteroom's venv, from vendoo-studio):
    .venv/bin/python ../vendoo-extension/tools/form-sweep/ebay/compare.py --db "<data dir>/vendoo_studio.db" \
        11483=ebay-model-11483.json 15687=ebay-model-15687.json

Each page file comes from page-model.js. The Taxonomy aspects for each category are read from
Anteroom's cache (the eBay tab fills it). Exits 1 when any category differs.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from vendoo_studio.services.native_editor import EBAY_SPECIFIC_SECTIONS, ebay_specifics_order

GROUPS = {"REQUIRED": EBAY_SPECIFIC_SECTIONS["required"], "RECOMMENDED": EBAY_SPECIFIC_SECTIONS["recommended"],
          "ADDITIONAL": EBAY_SPECIFIC_SECTIONS["optional"]}


def cached_aspects(db: str, category: str) -> list[dict]:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = connection.execute("SELECT fields FROM category_field_schemas WHERE category_id = ? AND marketplace LIKE 'ebay_aspects%' "
                             "ORDER BY marketplace DESC LIMIT 1", (category,)).fetchone()
    if not row:
        raise SystemExit(f"No cached eBay aspects for {category}. Open a listing in that category on the eBay tab first.")
    return json.loads(row[0])


def differences(page: list[dict], aspects: list[dict]) -> list[str]:
    ours = ebay_specifics_order(aspects)
    section = {row["name"]: EBAY_SPECIFIC_SECTIONS["required" if row.get("required") else
                                                    "recommended" if row.get("usage") == "RECOMMENDED" else "optional"]
               for row in ours}
    found = []
    if [row["name"] for row in page] != [row["name"] for row in ours]:
        found.append(f"order: page {[row['name'] for row in page]} / Anteroom {[row['name'] for row in ours]}")
    rows = {row["name"]: row for row in ours}
    for item in page:
        row = rows.get(item["name"])
        if row is None:
            continue
        if GROUPS[item["group"]] != section[item["name"]]:
            found.append(f"{item['name']}: page {item['group']}, Anteroom {section[item['name']]}")
        if item["multi"] != row["multi"]:
            found.append(f"{item['name']}: multi page {item['multi']}, Anteroom {row['multi']}")
        selection = row["mode"] == "selection" or (item["name"] == "Size" and bool(row["values"]))
        if item["custom"] is not None and item["custom"] == selection:
            found.append(f"{item['name']}: page {'takes' if item['custom'] else 'refuses'} typed values")
        options = [value["label"] for value in row["values"]]
        if item["options"] != options:
            found.append(f"{item['name']}: choices differ (page {len(item['options'])}, Anteroom {len(options)})")
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("pages", nargs="+", help="CATEGORY_ID=page-model.json")
    args = parser.parse_args()
    failed = False
    for pair in args.pages:
        category, path = pair.split("=", 1)
        found = differences(json.load(open(path)), cached_aspects(args.db, category))
        print(f"{category}: {'match' if not found else ''}")
        for line in found:
            print(f"  {line}")
        failed |= bool(found)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
