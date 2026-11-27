#!/usr/bin/env python3
"""
Convert DA "Bantay Presyo" price monitoring PDFs into CSV files, one per PDF,
in a "Per Market" folder created in the current folder (or --outdir).

Drag PDFs onto this file, or from a terminal:
    python pdf_to_csv.py a.pdf b.pdf
    python pdf_to_csv.py --all                  # every PDF in the current folder
    python pdf_to_csv.py input.pdf -o prices.csv   # single file only

Requires:  pip install pdfplumber
"""

import argparse
import csv
import re
import sys
import time
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
MARKET_DIR = "Per Market"          # output folder names


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


def pause():
    """Keep the window open when launched by drag-and-drop / double-click."""
    if sys.platform == "win32" and sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to close...")


def find_pdfs(folder, recursive):
    it = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(p for p in it if p.is_file() and p.suffix.lower() == ".pdf")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="*", help="one or more input PDF files")
    ap.add_argument("-o", "--output", help="output CSV (only with a single PDF)")
    ap.add_argument("--all", action="store_true",
                    help="convert every PDF in the current folder")
    ap.add_argument("--outdir", default=".",
                    help="folder in which to create 'Per Market' "
                         "(default: current folder)")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="with --all, also search subfolders")
    args = ap.parse_args()

    if not args.pdfs and not args.all:
        # Double-clicked with nothing dropped: offer to convert the whole folder.
        if sys.stdin and sys.stdin.isatty():
            print("No files given. Drag PDFs onto this script, or convert them all.")
            if input(f"Convert every PDF in {Path.cwd()}? [Y/n] ").strip().lower() in ("", "y", "yes"):
                args.all = True
        if not args.all:
            pause()
            return

    if args.all:
        found = find_pdfs(Path.cwd(), args.recursive)
        args.pdfs = [str(p) for p in found] + args.pdfs
        print(f"Found {len(found)} PDF(s) in {Path.cwd()}"
              f"{' (including subfolders)' if args.recursive else ''}", file=sys.stderr)
    if not args.pdfs:
        print("No PDF files found.")
        pause()
        return
    if args.output and len(args.pdfs) > 1:
        sys.exit("-o/--output can only be used with a single PDF.")

    market_dir = Path(args.outdir) / MARKET_DIR
    pdfs = list(dict.fromkeys(args.pdfs))
    start = time.time()
    done, failed = 0, 0
    for n, pdf in enumerate(pdfs, 1):
        tag = f"[{n}/{len(pdfs)}] {Path(pdf).name}"
        warnings = []
        try:
            report_date, order, markets = convert(pdf, warnings.append)
        except Exception as e:
            failed += 1
            print(f"{tag}  FAILED: {e}", file=sys.stderr)
            continue
        for w in warnings:
            print(f"{tag}  ! {w}", file=sys.stderr)
        out = Path(args.output) if args.output else market_dir / (Path(pdf).stem + ".csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        write_wide(out, report_date, order, markets)
        done += 1
        print(f"{tag}  ok ({len(markets)} markets)", file=sys.stderr)

    print(f"\nDone: {done} converted, {failed} failed in {time.time() - start:.1f}s.",
          file=sys.stderr)
    pause()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
