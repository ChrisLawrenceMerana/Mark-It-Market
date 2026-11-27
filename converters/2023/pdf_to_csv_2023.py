import argparse
import csv
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    import pdfplumber
    import pdfplumber.page
except ImportError:
    sys.exit("Missing dependency. Run:  pip install pdfplumber")
if not hasattr(pdfplumber.page.Page, "search"):
    sys.exit("pdfplumber is too old. Run:  pip install --upgrade pdfplumber")

NA = r"(?:NOT\s+AVAIL\w*|N/A)"        # used if the PDFs misspell "NOT AVAILBLE"
# A price cell: "45.00", "45.00 - 47.00", "45.00 -47.00", or NOT AVAILABLE
TOKEN = rf"(?:{NA}(?:\s*-\s*{NA})?|\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)"
ROW_RE = re.compile(rf"^(?P<market>.+?)\s+(?P<values>{TOKEN}(?:\s+{TOKEN})*)\s*$")
TOKEN_RE = re.compile(TOKEN)

# Pork / chicken pages: "N/A" = not available in the market, "n/m" = no monitoring done
RANGE = r"\d+(?:\.\d+)?(?:\s*-\s*|\s+)\d+(?:\.\d+)?"   # if the dash is missing
PTOKEN = rf"(?:{RANGE}|(?:N/A|n/m)(?:\s*-\s*(?:N/A|n/m))?)"   # "N/A - N/A" can occur
PROW_RE = re.compile(rf"^(?:\d+\s+)?(?P<market>.+?)\s+(?P<values>{PTOKEN}(?:\s+{PTOKEN})*)\s*$",
                     re.IGNORECASE)
PTOKEN_RE = re.compile(PTOKEN, re.IGNORECASE)

# The PDF writes the date as "Tuesday, December 26, 2023" on some pages and
# "Tuesday, 26 December 2023" on others. Both are accepted.
WEEKDAY = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
DATE_RE = re.compile(
    rf"{WEEKDAY},?\s+(?:(?P<d1>\d{{1,2}})\s+(?P<m1>[A-Za-z]+)\s+(?P<y1>\d{{4}})"
    rf"|(?P<m2>[A-Za-z]+)\s+(?P<d2>\d{{1,2}}),?\s+(?P<y2>\d{{4}}))")
FILE_DATE_RE = re.compile(r"([A-Za-z]+)[-_ ](\d{1,2})[-_ ](\d{4})")

MARKET_PAGE_MARK = "COMMODITY (PHP/KG)"     # the per-market table of selected commodities
PROTEIN_PAGE_MARK = "PRICE PER KILOGRAM"    # the per-market pork and chicken pages
SUMMARY_PAGE_MARK = "at NCR Markets"        # the one-page NCR-wide infographic
MARKET_DIR = "Per Market"                   # output folder names
SUMMARY_DIR = "NCR Summary"

# For parsing
def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def parse_date(raw):
    for f in ("%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def find_date(text):
    m = DATE_RE.search(text)
    if not m:
        return ""
    raw = (f"{m['d1']} {m['m1']} {m['y1']}" if m["d1"]
           else f"{m['m2']} {m['d2']} {m['y2']}")
    return parse_date(raw) or raw


def filename_date(path):
    """'Price-Monitoring-December-26-2023.pdf' -> '2023-12-26' (or '')."""
    m = FILE_DATE_RE.search(Path(path).stem)
    return (parse_date(f"{m[1]} {m[2]} {m[3]}") or "") if m else ""


def fix_line(line):
    """Un-glue cells that the PDF text runs together: 'NOT AVAILABLE110.00 -120.00',
    'NOT AVAILABLENOT AVAILABLE', '0.00n/m0.00'."""
    line = re.sub(r"(?<=[A-Za-z0-9])(?=NOT\s+AVAIL)", " ", line)
    line = re.sub(r"(AVAIL[A-Z]*)(?=\d)", r"\1 ", line)
    line = re.sub(r"(?<=\d)(?=n/m|N/A)", " ", line)
    line = re.sub(r"(?<=n/m)(?=\d)|(?<=N/A)(?=\d)", " ", line)
    return line


def parse_price(cell):
    """'45.00 - 47.00' -> (45.0, 47.0); '45.00' -> (45.0, 45.0); N/A, n/m -> (None, None)."""
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", clean(cell))]
    if not nums:
        return None, None
    return (nums[0], nums[0]) if len(nums) == 1 else (min(nums), max(nums))


# Per-market table of selected commodities (page "... per Market")
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
    rows, problems = [], []
    for line in text.splitlines():
        line = clean(fix_line(line))
        if DATE_RE.search(line):
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        cells = [t.group(0) for t in TOKEN_RE.finditer(m.group("values"))]
        if len(cells) != len(commodities):
            problems.append((clean(m.group("market")), len(cells)))
            continue
        rows.append((clean(m.group("market")), cells))
    # Some rows cannot be read from the text lines (a cell that wraps onto two lines, a
    # blank cell). The table reader sees them cell by cell, so use it for those rows.
    have = {market for market, _ in rows}
    recovered = []
    for table in page.extract_tables():
        for row in table:
            market = clean(row[0]) if row else ""
            raw = row[0] if row and row[0] else ""
            if (len(row) - 1 == len(commodities) and raw.startswith("NOT\n")
                    and raw.rstrip().endswith("AVAILABLE") and not any(row[1:])):
                # whole row squeezed into one cell with a wrapped leading NOT AVAILABLE
                body = clean(fix_line(raw.strip()[4:].rstrip()[:-len("AVAILABLE")]))
                m = ROW_RE.match(body)
                if m:
                    vals = [t.group(0) for t in TOKEN_RE.finditer(m.group("values"))]
                    name = clean(m.group("market"))
                    if len(vals) == len(commodities) - 1 and name not in have:
                        rows.append((name, ["NOT AVAILABLE"] + vals))
                        recovered.append(name)
                        continue
            if (not market or market.upper() == "MARKET" or market in have
                    or len(row) - 1 != len(commodities)):
                continue
            cells = [clean(c) for c in row[1:]]
            if any(re.search(r"\d", c) for c in cells):
                rows.append((market, cells))
                recovered.append(market)
    for market, found in problems:
        if not any(market.startswith(r) for r in recovered):
            warn(f"skipped '{market}': expected {len(commodities)} values, found {found}")
    return commodities, rows


# Per-market pork and chicken pages (one commodity group per page)
def parse_protein_page(text, warn):
    """Pages titled e.g. 'FRESH & FROZEN PORK LIEMPO' or 'FRESH WHOLE CHICKEN'.

    Returns ([(commodity, unit), ...], [(market, [cell, ...]), ...]). A page with
    "Fresh" and "Frozen" columns gives two commodities ('Fresh Pork Liempo' and
    'Frozen Pork Liempo'). Markets marked n/m in every column were not monitored and
    are left out.
    """
    lines = [re.sub(r"(?<=[\d/A])\s+[A-Za-z]$", "", clean(fix_line(l)))   # stray glyph at line end
             for l in text.splitlines()]
    heading = ""
    for line in lines:                          # last all-caps line above "PRICE PER KILOGRAM"
        if PROTEIN_PAGE_MARK in line.upper():
            break
        if line.isupper() and not DATE_RE.search(line):
            heading = line
    if not heading:
        warn("pork/chicken page without a recognisable heading, skipped")
        return [], []
    m = re.match(r"(\w+)\s*&\s*(\w+)\s+(.+)$", heading)
    if m:                                       # "FRESH & FROZEN PORK LIEMPO"
        names = [f"{m[1].title()} {m[3].title()}", f"{m[2].title()} {m[3].title()}"]
    else:                                       # "FRESH WHOLE CHICKEN"
        names = [heading.title()]
    rows = []
    for line in lines:
        if DATE_RE.search(line):
            continue
        r = PROW_RE.match(line)
        if not r:
            if re.match(r"^\d+\s+[A-Za-z]", line) and not line.upper().startswith("PRICE"):
                warn(f"skipped unreadable row '{line}'")
            continue
        cells = [t.group(0) for t in PTOKEN_RE.finditer(r.group("values"))]
        if len(cells) != len(names):
            warn(f"skipped '{r.group('market')}': expected {len(names)} "
                 f"values, found {len(cells)}")
            continue
        if all(re.fullmatch(r"n/m(?:\s*-\s*n/m)?", c, re.IGNORECASE) for c in cells):
            continue                            # not monitored that day
        rows.append((clean(r.group("market")), cells))
    return [(n, "PHP/kg") for n in names], rows


# The pork/chicken pages list markets under slightly different names than the main
# table ("Grace Marketplace (Pateros)" vs "Pateros Market"). Their rows are joined to
# the main table's market by name. Names are compared ignoring case, spacing and a
# trailing "/Place" or "(Place)". Anything else that is the same market needs an entry
# here (left side lower case, as printed on the pork/chicken pages). A market that
# cannot be matched is kept under its own name and reported with a warning.
MARKET_ALIASES = {
    "grace marketplace (pateros)": "Pateros Market",
    "mutya ng pasig mega market": "Pasig City Mega Market",
    "mandaluyong public market i": "Mandaluyong Public Market",
    "kamuning market": "Kamuning Public Market",
    "la huerta new parañaque market": "La Huerta Market/Parañaque",
}


def market_key(name):
    return re.sub(r"\s*[/(].*$", "", clean(name)).lower()


def resolve_market(name, known):
    """Map a pork/chicken page market name to the name used in the main table."""
    if clean(name).lower() in {k.lower() for k in known}:
        return next(k for k in known if k.lower() == clean(name).lower())
    target = MARKET_ALIASES.get(clean(name).lower())
    if target:
        return next((k for k in known if k.lower() == target.lower()), target)
    keys = {market_key(k): k for k in known}
    return keys.get(market_key(name))
