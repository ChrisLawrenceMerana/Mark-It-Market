#!/usr/bin/env python3
"""
Convert a DA "Bantay Presyo" price monitoring PDF into a CSV file.

    python pdf_to_csv.py input.pdf        # writes input.csv next to the PDF

Requires:  pip install pdfplumber
"""

import csv
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("Missing dependency. Run:  pip install pdfplumber")

NA = "NOT AVAILABLE"
# A price cell: "45.00", "45.00 - 47.00", "45.00 -47.00", or NOT AVAILABLE
TOKEN = rf"(?:{NA}|\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)"
ROW_RE = re.compile(rf"^(?P<market>.+?)\s+(?P<values>{TOKEN}(?:\s+{TOKEN})*)\s*$")
TOKEN_RE = re.compile(TOKEN)
DATE_RE = re.compile(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
                     r"(\d{1,2}\s+\w+\s+\d{4})")
MARKET_PAGE_MARK = "COMMODITY (PHP/KG)"

# ----------------------------------------------------------------- parsing
def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def has_date(line):
    """True for the heading line that carries the report date (not a data row)."""
    return bool(DATE_RE.search(line))

def iso_date(raw):
    try:
        return datetime.strptime(raw, "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        return raw

def parse_price(cell):
    """'45.00 - 47.00' -> (45.0, 47.0); '45.00' -> (45.0, 45.0); N/A -> (None, None)."""
    cell = clean(cell)
    if not cell or cell.upper() == NA:
        return None, None
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", cell)]
    return (nums[0], nums[0]) if len(nums) == 1 else (min(nums), max(nums))

def get_commodities(page):
    """Commodity names from the table header (the row starting with MARKET)."""
    for table in page.extract_tables():
        for row in table:
            if row and clean(row[0]).upper() == "MARKET":
                # Merged cells can leave stray empty columns; drop them.
                return [clean(c) for c in row[1:] if clean(c)]
    return None

def parse_market_page(page, text, warn):
    commodities = get_commodities(page)
    if not commodities:
        return None, []
    rows = []
    for line in text.splitlines():
        line = clean(line)
        if has_date(line):
            continue
        # cells can be glued together without spaces: "...AVAILABLENOT AVAILABLE100.00-160.00"
        line = re.sub(r"(NOT AVAILABLE)(?=\S)", r"\1 ", line)
        line = re.sub(r"(?<=\d)(?=NOT AVAILABLE)", " ", line)
        m = ROW_RE.match(line)
        if not m:
            continue
        cells = [t.group(0) for t in TOKEN_RE.finditer(m.group("values"))]
        if len(cells) != len(commodities):
            warn(f"skipped '{m.group('market')}': expected {len(commodities)} "
                 f"values, found {len(cells)}")
            continue
        market = clean(m.group("market"))
        rows.append((market, cells))
    return commodities, rows

def convert(pdf_path, warn=lambda msg: None):
    """Open the PDF once. Returns (iso_date, commodity_order, markets)."""
    markets, order = {}, []
    report_date = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if not report_date:
                m = DATE_RE.search(text)
                if m:
                    report_date = iso_date(m.group(2))
            commodities, rows = parse_market_page(page, text, warn)
            if not commodities:
                continue
            names = []
            for c in commodities:                # a leading '*' means "per piece"
                unit = "PHP/piece" if c.startswith("*") else "PHP/kg"
                names.append((c.lstrip("*").strip(), unit))
            order.extend(n for n in names if n not in order)
            for market, cells in rows:
                prices = markets.setdefault(market, {})
                for name, cell in zip(names, cells):
                    prices[name] = parse_price(cell)
    return report_date, order, markets

# ----------------------------------------------------------------- writing
def fmt(x):
    return "" if x is None else f"{x:.2f}"


def write_wide(path, report_date, order, markets):
    header = ["date", "market"]
    for name, unit in order:
        header += [f"{name} Low ({unit})", f"{name} High ({unit})"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for market, prices in markets.items():
            row = [report_date, market]
            for key in order:
                low, high = prices.get(key, (None, None))
                row += [fmt(low), fmt(high)]
            w.writerow(row)

def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python pdf_to_csv.py input.pdf")
    pdf = Path(sys.argv[1])
    report_date, order, markets = convert(pdf, print)
    out = pdf.with_suffix(".csv")
    write_wide(out, report_date, order, markets)
    print(f"Wrote {out} ({len(markets)} markets)")

if __name__ == "__main__":
    main()
