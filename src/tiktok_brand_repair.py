"""Audit and repair the Brand column in TikTok Shop bulk-edit templates.

Why this exists
---------------
TikTok Shop treats Brand as a required field on most categories, but the
Seller Centre UI does not tell you when it is missing. A product with no Brand
value will, all at once:

  * show as Deactivated with no stated reason
  * fail bulk activation with a generic "System error"
  * report Sync failed when pushed from a connected storefront

Three symptoms, three different screens, one missing field. It is easy to spend
a day treating them as separate problems. Check Brand first.

The second failure mode is contamination. If your listing tool derives Brand
from the design or product name, any word resembling a trademark gets mapped to
that trademark -- a shirt called "Champion of the Week" acquires the brand
CHAMPION. TikTok then blocks all edits to that listing with "Unauthorised
brand", and the listing can end up locked. Audit for this on a schedule.

The fix for both is a bulk-edit template round trip: export from Seller Centre,
correct the Brand column, re-upload. This script does the middle part.

Usage::

    # what is wrong
    python src/tiktok_brand_repair.py audit export.xlsx

    # write corrected upload files
    python src/tiktok_brand_repair.py repair export.xlsx \\
        --brand "Your Brand Name" --out-dir uploads --chunk-size 100

Notes
-----
Column names differ by region and template version, so they are configurable
rather than hardcoded. Run ``audit`` first: it prints the columns it detected.

Seller Centre rejects oversized uploads without a useful message. Chunks of
roughly 100 rows upload reliably; raise it only if yours are smaller.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    sys.exit("This script needs pandas and openpyxl: pip install -r requirements.txt")


# Brands that commonly appear through name-derivation rather than intent.
# Extend this for your own catalogue -- it is a starting point, not a whitelist.
COMMON_FALSE_BRANDS = {
    "champion", "levi", "levis", "levi's", "jeep", "shimano", "gildan",
    "nike", "adidas", "puma", "reebok", "vans", "converse", "supreme",
    "patagonia", "columbia", "carhartt", "dickies", "hanes", "fruit of the loom",
    "bella", "canvas", "next level", "american apparel", "polo", "lacoste",
}

BRAND_COLUMN_CANDIDATES = ["brand", "brand name", "product brand"]
ID_COLUMN_CANDIDATES = ["product id", "product_id", "sku id", "seller sku", "sku"]
NAME_COLUMN_CANDIDATES = ["product name", "product_name", "title"]


def load(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str)
    return pd.read_csv(path, dtype=str)


def find_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    lookup = {str(col).strip().lower(): col for col in frame.columns}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    for key, original in lookup.items():
        if any(candidate in key for candidate in candidates):
            return original
    return None


def normalise(value: object) -> str:
    return re.sub(r"[^a-z0-9' ]", "", str(value or "").strip().lower())


def audit(path: Path, authorised: list[str] | None = None) -> int:
    frame = load(path)
    brand_col = find_column(frame, BRAND_COLUMN_CANDIDATES)
    id_col = find_column(frame, ID_COLUMN_CANDIDATES)
    name_col = find_column(frame, NAME_COLUMN_CANDIDATES)

    print(f"Rows: {len(frame)}")
    print(f"Detected brand column: {brand_col or 'NOT FOUND'}")
    print(f"Detected id column:    {id_col or 'not found'}")
    print(f"Detected name column:  {name_col or 'not found'}")

    if not brand_col:
        print("\nNo brand column. Pass --brand-column with the exact header.")
        print(f"Available columns: {list(frame.columns)}")
        return 1

    blank = frame[frame[brand_col].isna() | (frame[brand_col].astype(str).str.strip() == "")]
    print(f"\nMissing brand: {len(blank)} rows")

    allowed = {normalise(item) for item in (authorised or [])}
    counts = frame[brand_col].fillna("").value_counts()

    suspicious: list[tuple[str, int]] = []
    for value, count in counts.items():
        key = normalise(value)
        if not key:
            continue
        if allowed and key in allowed:
            continue
        if key in COMMON_FALSE_BRANDS or (allowed and key not in allowed):
            suspicious.append((str(value), int(count)))

    print(f"Distinct brand values: {len([v for v in counts.index if str(v).strip()])}")
    if suspicious:
        print("\nPossible contamination:")
        for value, count in sorted(suspicious, key=lambda item: -item[1]):
            print(f"  {count:>6}  {value}")
    else:
        print("\nNo obviously unauthorised brand values found.")

    return 0


def repair(
    path: Path,
    brand: str,
    out_dir: Path,
    chunk_size: int,
    brand_column: str | None,
    only_missing: bool,
) -> int:
    frame = load(path)
    brand_col = brand_column or find_column(frame, BRAND_COLUMN_CANDIDATES)
    if not brand_col:
        print("No brand column found. Pass --brand-column.")
        return 1

    if only_missing:
        mask = frame[brand_col].isna() | (frame[brand_col].astype(str).str.strip() == "")
        target = frame[mask].copy()
    else:
        target = frame.copy()

    if target.empty:
        print("Nothing to repair.")
        return 0

    target[brand_col] = brand
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for index, start in enumerate(range(0, len(target), chunk_size), start=1):
        chunk = target.iloc[start : start + chunk_size]
        destination = out_dir / f"brand_fix_{index:03d}.xlsx"
        chunk.to_excel(destination, index=False)
        print(f"  wrote {destination} ({len(chunk)} rows)")
        written += len(chunk)

    print(f"\n{written} rows across {index} file(s) in {out_dir}/")
    print("Upload these through Seller Centre > Manage Products > Bulk edit.")
    print("Upload one file at a time and wait for each to finish processing.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    audit_parser = sub.add_parser("audit", help="report missing and suspicious brands")
    audit_parser.add_argument("file", type=Path)
    audit_parser.add_argument(
        "--authorised",
        nargs="*",
        default=None,
        help="brand names authorised on your account; anything else is flagged",
    )

    repair_parser = sub.add_parser("repair", help="write corrected upload files")
    repair_parser.add_argument("file", type=Path)
    repair_parser.add_argument("--brand", required=True, help="brand to write into every row")
    repair_parser.add_argument("--out-dir", type=Path, default=Path("uploads"))
    repair_parser.add_argument("--chunk-size", type=int, default=100)
    repair_parser.add_argument("--brand-column", default=None)
    repair_parser.add_argument(
        "--all-rows",
        action="store_true",
        help="overwrite every row, not just rows with a blank brand",
    )

    args = parser.parse_args()

    if args.command == "audit":
        return audit(args.file, args.authorised)
    return repair(
        args.file,
        brand=args.brand,
        out_dir=args.out_dir,
        chunk_size=args.chunk_size,
        brand_column=args.brand_column,
        only_missing=not args.all_rows,
    )


if __name__ == "__main__":
    raise SystemExit(main())
