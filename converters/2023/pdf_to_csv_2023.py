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


