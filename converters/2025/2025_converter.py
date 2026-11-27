import argparse 
import csv
import os
import re
import sys
import time

from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pdfplumber

NA = "NOT AVAILABLE"

TOKEN = rf"(?:{NA}|\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)"
ROW_RE = re.compile(rf"^(?P<market>.+?)\s+(?P<values>{TOKEN}(?:\s+{TOKEN})*)\s*$")
TOKEN_RE = re.compile(TOKEN)

DATE_RE = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
    r"(\d{1,2}\s+\w+\s+\d{4})"
)

MARKET_DIR = "Per Market"
SUMMARY_DIR = "NCR Summary"

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def plausible_market(name):
    return (3 <= len(name) <= 60
            and not re.search(r"\d", name)
            and "NOT AVAIL" not in name.upper())

def plausible_commodity(name):
    return (2 <= len(name) <= 50
            and not re.search(r"\d+\.\d{2}", name)
            and "NOT AVAIL" not in name.upper())

# Month names used for date detection
_MON = (
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

# Date patterns
TEXT_DATE_PATTERNS = [
    (re.compile(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[ \t]+{_MON}\.?,?[ \t]+(\d{{4}})\b",
        re.I
    ), "dmy"),

    (re.compile(
        rf"\b{_MON}\.?[ \t]+(\d{{1,2}})(?:st|nd|rd|th)?,?[ \t]+(\d{{4}})\b",
        re.I
    ), "mdy"),
]

# Convert month abbreviations into numbers
_MONTH_NUM = {
    month: i for i, month in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1
    )
}

def find_report_date(text):
    """Find the report date and return it in YYYY-MM-DD format."""
    found = []

    for pattern, order in TEXT_DATE_PATTERNS:
        for match in pattern.finditer(text):
            a, b, c = match.groups()

            if order == "dmy":
                month, day = b, a
            else:
                month, day = a, b

            try:
                date = datetime(
                    int(c),
                    _MONTH_NUM[month[:3].lower()],
                    int(day)
                )

                found.append((match.start(), date.strftime("%Y-%m-%d")))

            except (ValueError, KeyError):
                continue

    return min(found)[1] if found else ""

def has_date(line):
    """True for the heading line that carries the report date."""
    return bool(DATE_RE.search(line) or find_report_date(line))

def iso_date(raw):
    try:
        return datetime.strptime(raw, "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        return raw