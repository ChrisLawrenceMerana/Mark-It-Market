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