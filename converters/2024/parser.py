"""
parser.py

Extracts from DA Bantay Presyo PDFs:
1. Per-market price grid -> parse_market_page()
   Columns: date, market, city, commodity, unit, price_min, price_max, available
2. Daily NCR summary -> parse_daily_summary()
   Columns: date, group, commodity, unit, low, high, prevailing, average,
            market_count, prevailing_source, average_source

Inspect a file from the terminal:
    python parser.py "path/to/report.pdf"
"""

from __future__ import annotations

import bisect
import math
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

EXPECTED_MARKETS: int | None = None
MIN_READABLE_CHARS = 15

MARKET_COLUMNS = [
    "date",
    "market",
    "city",
    "commodity",
    "unit",
    "price_min",
    "price_max",
    "available",
]

SUMMARY_COLUMNS = [
    "date",
    "group",
    "commodity",
    "unit",
    "low",
    "high",
    "prevailing",
    "average",
    "market_count",
    "prevailing_source",
    "average_source",
]

# (clean name, unit, header keywords, summary group)
COMMODITIES = [
    ("Well-milled Rice (Local)", "PHP/kg", ("rice",), "Local Commercial Rice"),
    ("Corn (White)", "PHP/kg", ("corn",), "Corn"),
    ("Corn (Yellow)", "PHP/kg", ("corn",), "Corn"),
    ("Tilapia", "PHP/kg", ("tilapia",), "Fish"),
    ("Galunggong", "PHP/kg", ("galungg",), "Fish"),
    ("Egg (Medium)", "PHP/piece", ("egg",), "Livestock And Poultry Products"),
    ("Ampalaya", "PHP/kg", ("ampalaya",), "Lowland Vegetables"),
    ("Tomato", "PHP/kg", ("tomato",), "Lowland Vegetables"),
    ("Cabbage (Rareball)", "PHP/kg", ("rare",), "Highland Vegetables"),
    ("Cabbage (Scorpio)", "PHP/kg", ("scorp",), "Highland Vegetables"),
    ("Cabbage (Wonderball)", "PHP/kg", ("wonder",), "Highland Vegetables"),
    ("Pechay Baguio", "PHP/kg", ("pech",), "Highland Vegetables"),
    ("Red Onion (Local)", "PHP/kg", ("onion",), "Spices"),
    ("Sugar (Washed)", "PHP/kg", ("sugar",), "Other Commodities"),
    ("Fresh Pork Kasim/Pigue", "PHP/kg", ("kasim",), "Livestock And Poultry Products"),
    ("Frozen Pork Kasim/Pigue", "PHP/kg", ("kasim",), "Livestock And Poultry Products"),
    ("Fresh Pork Liempo", "PHP/kg", ("liempo",), "Livestock And Poultry Products"),
    ("Frozen Pork Liempo", "PHP/kg", ("liempo",), "Livestock And Poultry Products"),
    ("Fresh Whole Chicken", "PHP/kg", ("chicken",), "Livestock And Poultry Products"),
]

# Page-1 infographic labels mapped to (group, commodity, unit, regex)
SUMMARY_ITEMS_2024 = [
    ("Local Commercial Rice", "Special", "PHP/kg", r"\bSpecial\b"),
    ("Local Commercial Rice", "Premium", "PHP/kg", r"\bPremium\b"),
    ("Local Commercial Rice", "Well-milled Rice (Local)", "PHP/kg", r"\bWell-milled\b"),
    ("Local Commercial Rice", "Regular Milled", "PHP/kg", r"\bRegular Milled\b"),
    ("Corn", "Corn (White)", "PHP/kg", r"Corn\s*\(White\)"),
    ("Corn", "Corn (Yellow)", "PHP/kg", r"Corn\s*\(Yellow\)"),
    ("Livestock And Poultry Products", "Beef Rump", "PHP/kg", r"Beef\s*Rump"),
    ("Livestock And Poultry Products", "Beef Brisket", "PHP/kg", r"Beef\s*Brisket"),
    ("Livestock And Poultry Products", "Fresh Pork Kasim/Pigue", "PHP/kg", r"\bPork\s*Kasim\b"),
    ("Livestock And Poultry Products", "Fresh Pork Liempo", "PHP/kg", r"\bPork\s*Liempo\b"),
    ("Livestock And Poultry Products", "Frozen Pork Kasim/Pigue", "PHP/kg", r"\bFrozen\s*Kasim\b"),
    ("Livestock And Poultry Products", "Frozen Pork Liempo", "PHP/kg", r"\bFrozen\s*Liempo\b"),
    ("Livestock And Poultry Products", "Fresh Whole Chicken", "PHP/kg", r"\bWhole\s*Chicken\b"),
    ("Livestock And Poultry Products", "Egg (Medium)", "PHP/piece", r"Egg\s*\(White,?\s*Medium\)"),
    ("Fish", "Milkfish (Bangus)", "PHP/kg", r"Milkfish\s*Bangus"),
    ("Fish", "Tilapia", "PHP/kg", r"\bTilapia\b"),
    ("Fish", "Galunggong", "PHP/kg", r"Local\s*Round\s*Scad\s*Galungg\w+"),
    ("Fish", "Indian Mackerel (Alumahan)", "PHP/kg", r"Indian\s*mackerel\s*Alumahan"),
    ("Lowland Vegetables", "Ampalaya", "PHP/kg", r"Bittergourd\s*Ampalaya"),
    ("Lowland Vegetables", "String Beans (Sitao)", "PHP/kg", r"String\s*Beans\s*Sitao"),
    ("Lowland Vegetables", "Pechay Tagalog", "PHP/kg", r"Pechay\s*Tagalog"),
    ("Lowland Vegetables", "Squash (Kalabasa)", "PHP/kg", r"Squash\s*Kalabasa"),
    ("Lowland Vegetables", "Eggplant (Talong)", "PHP/kg", r"Eggplant\s*Talong"),
    ("Lowland Vegetables", "Tomato", "PHP/kg", r"Tomato\s*Kamatis"),
    ("Highland Vegetables", "Cabbage (Rareball)", "PHP/kg", r"Rare\s*Ball"),
    ("Highland Vegetables", "Cabbage (Scorpio)", "PHP/kg", r"\bScorpio\b"),
    ("Highland Vegetables", "Cabbage (Wonderball)", "PHP/kg", r"Wonder\s*Ball"),
    ("Highland Vegetables", "Carrot (Karot)", "PHP/kg", r"Carrot\s*Karot"),
    ("Highland Vegetables", "Baguio Beans", "PHP/kg", r"Baguio\s*Beans"),
    ("Highland Vegetables", "White Potato (Patatas)", "PHP/kg", r"White\s*Potato"),
    ("Highland Vegetables", "Pechay Baguio", "PHP/kg", r"Pechay\s*Baguio"),
    ("Highland Vegetables", "Chayote (Sayote)", "PHP/kg", r"Chayote\s*Sayote"),
    ("Highland Vegetables", "Cauliflower", "PHP/kg", r"\bCauliflower\b"),
    ("Fruits", "Calamansi", "PHP/kg", r"\bCalamansi\b"),
    ("Fruits", "Banana (Lakatan)", "PHP/kg", r"Banana\s*\(Lakatan\)"),
    ("Fruits", "Banana (Latundan)", "PHP/kg", r"Banana\s*\(Latundan\)"),
    ("Fruits", "Banana (Saba)", "PHP/kg", r"Banana\s*\(Saba\)"),
    ("Fruits", "Papaya", "PHP/kg", r"\bPapaya\b"),
    ("Fruits", "Mango (Carabao)", "PHP/kg", r"Mango\s*\(Carabao\)"),
    ("Fruits", "Watermelon", "PHP/kg", r"\bWatermelon\b"),
    ("Other Commodities", "Sugar (Refined)", "PHP/kg", r"Sugar\s*Refined"),
    ("Other Commodities", "Sugar (Washed)", "PHP/kg", r"Sugar\s*Washed"),
    ("Other Commodities", "Sugar (Brown)", "PHP/kg", r"Sugar\s*Brown"),
    ("Other Commodities", "Palm Oil - 350ml", "PHP/bottle", r"Palm\s*Oil\s*-\s*350ml"),
    ("Other Commodities", "Palm Oil - 1L", "PHP/bottle", r"Palm\s*Oil\s*-\s*1L"),
    ("Other Commodities", "Coconut Oil - 350ml", "PHP/bottle", r"Coconut\s*Oil\s*-\s*350ml"),
    ("Other Commodities", "Coconut Oil - 1L", "PHP/bottle", r"Coconut\s*Oil\s*-\s*1L"),
    ("Spices", "Red Onion (Local)", "PHP/kg", r"Local\s*Red\s*Onion"),
    ("Spices", "Local White Onion", "PHP/kg", r"Local\s*White\s*Onion"),
    ("Spices", "Imported Garlic", "PHP/kg", r"Imported\s*Garlic"),
    ("Spices", "Local Garlic", "PHP/kg", r"Local\s*Garlic"),
    ("Spices", "Ginger (Luya)", "PHP/kg", r"Ginger\s*Luya"),
    ("Spices", "Chili (Siling Labuyo)", "PHP/kg", r"Chil[il]\s*Siling\s*labuy\w+"),
]

GAP_TOLERANCES = (0, 0.5, 1, 1.5, 2, 3, 4, 6, 8, 10)
PRICE_FLOOR, PRICE_CEILING = 0, 10_000

DATE_RE = re.compile(
    r"(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)\w*,?\s+)?"
    r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})"
)
DAY_MONTH_DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})\b")
FILENAME_DATE_RE = re.compile(r"([A-Za-z]+)-(\d{1,2})-(\d{4})", re.IGNORECASE)
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
RANGE_OR_STATUS_RE = re.compile(
    r"(?:NOT\s+AVAIL(?:ABLE)?|TAVAIL\w*|\d[\d,]*(?:\.\d+)?\s*(?:-\s*\d[\d,]*(?:\.\d+)?)?)",
    re.IGNORECASE,
)


class ParseError(Exception):
    """Raised when the PDF does not look like the layout this parser expects."""


class SkippedImagePDFError(ParseError):
    """Raised when a PDF has no readable/selectable text and appears to be an image."""