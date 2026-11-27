"""
Parse the 2021 DA Bantay Presyo PDFs into the same two CSV layouts as your groupmate's files.

  ncr_summary_2021.csv  ->  date, group, commodity, unit, low, high, prevailing, average, source_file
  markets_2021.csv      ->  date, market, commodity, unit, price_low, price_high, source_file

The 2021 PDFs come in three kinds, and this script handles each differently:

  1. "Tabled" (Jan - ~Aug 2021): page 1 is a text table with High / Low / Prevailing for every
     commodity. -> goes to ncr_summary_2021.csv. These files have NO per-market data.
  2. "Per-market" (~Sep - Dec 2021): one page lists the price of 10 commodities in each market.
     -> goes to markets_2021.csv (when that page has a text layer).
  3. Image-only pages (e.g. Oct 7/8, some Sep files): no text to read. They are listed in
     parse_report.txt as NEEDS OCR / MANUAL ENTRY.

Run it on the ORIGINAL PDFs (pdfs_raw), not pdfs_clean:

    cd pdfs_raw
    py ..\\daily_pdf_to_csv-main\\parse_2021.py . --extra ..\\daily_pdf_to_csv-main\\manual_oct7_oct8_markets.csv

Output files and parse_report.txt are written to the current folder.
"""
import argparse
import csv
import re
import statistics
import sys
from datetime import date
from pathlib import Path

import pdfplumber

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
FNAME_RE = re.compile(r"Price-Monitoring-([A-Za-z]+)-(\d{1,2})-(\d{4})", re.I)
PRICE_RE = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2}$")

# ----------------------------------------------------------------------------------------
# 1. NCR SUMMARY (tabled format)
# ----------------------------------------------------------------------------------------
VAL = r"(?:\d{1,3}(?:,\d{3})*\.\d{2}|NONE)"
LINE_RE = re.compile(rf"^(?P<label>.*?)(?:\s+(?P<hi>{VAL})\s+(?P<lo>{VAL})\s+(?P<pv>{VAL}))?\s*$")

GROUPS = {
    "NFA RICE": "NFA Rice",
    "IMPORTED COMMERCIAL RICE": "Imported Commercial Rice",
    "LOCAL COMMERCIAL RICE": "Local Commercial Rice",
    "FISH": "Fish",
    "LIVESTOCK & POULTRY PRODUCTS": "Livestock And Poultry Products",
    "LOWLAND VEGETABLES": "Lowland Vegetables",
    "HIGHLAND VEGETABLES": "Highland Vegetables",
    "SPICES": "Spices",
    "FRUITS": "Fruits",
    "OTHER BASIC COMMODITIES": "Other Commodities",
}
RICE = [
    (r"^Special", "Special", "PHP/kg"),
    (r"^Premium", "Premium", "PHP/kg"),
    (r"^Well milled", "Well Milled", "PHP/kg"),
    (r"^Regular milled", "Regular Milled", "PHP/kg"),
]
# (regex on the start of the label, name written to the CSV, unit) - first match wins
ITEMS = {
    "NFA Rice": [(r"^NFA", "NFA (Well Milled)", "PHP/kg")],
    "Imported Commercial Rice": RICE,
    "Local Commercial Rice": RICE,
    "Fish": [
        (r"^Bangus", "Milkfish (Bangus)", "PHP/kg"),
        (r"^Tilapia", "Tilapia", "PHP/kg"),
        (r"^Galunggong \(Local\)", "Local Round Scad (Galunggong)", "PHP/kg"),
        (r"^Galunggong \(Imported\)", "Imported Round Scad (Galunggong)", "PHP/kg"),
        (r"^Alumahan", "Alumahan", "PHP/kg"),
    ],
    "Livestock And Poultry Products": [
        (r"^Beef Rump", "Beef Rump", "PHP/kg"),
        (r"^Beef Brisket", "Beef Brisket", "PHP/kg"),
        (r"^Pork Ham", "Pork Ham", "PHP/kg"),
        (r"^Pork Belly", "Pork Belly", "PHP/kg"),
        (r"^Whole Chicken", "Whole Chicken", "PHP/kg"),
        (r"^Chicken Egg", "Chicken Egg (Medium)", "PHP/piece"),
    ],
    "Lowland Vegetables": [
        (r"^Ampalaya", "Bittergourd (Ampalaya)", "PHP/kg"),
        (r"^Sitao", "String Beans (Sitao)", "PHP/kg"),
        (r"^Pechay \(Native\)", "Pechay Tagalog", "PHP/kg"),
        (r"^Squash", "Squash (Kalabasa)", "PHP/kg"),
        (r"^Eggplant", "Eggplant (Talong)", "PHP/kg"),
        (r"^Tomato", "Tomato (Kamatis)", "PHP/kg"),
    ],
    "Highland Vegetables": [
        (r"^Cabbage", "Cabbage Repolyo (Scorpio)", "PHP/kg"),
        (r"^Carrots?", "Carrot (Karot)", "PHP/kg"),
        (r"^Habitchuelas", "Baguio Beans (Habitchuelas)", "PHP/kg"),
        (r"^White Potato", "White Potato (Patatas)", "PHP/kg"),
        (r"^Pechay \(Baguio\)", "Pechay Baguio", "PHP/kg"),
        (r"^Chayote", "Chayote (Sayote)", "PHP/kg"),
    ],
    "Spices": [
        (r"^Red Onion \(Imported\)", "Imported Red Onion", "PHP/kg"),
        (r"^Red Onion", "Local Red Onion (Pulang Sibuyas)", "PHP/kg"),
        (r"^White Onion \(Imported\)", "Imported White Onion", "PHP/kg"),
        (r"^White Onion", "Local White Onion (Puting Sibuyas)", "PHP/kg"),
        (r"^Garlic \(Imported\)", "Imported Garlic (Bawang)", "PHP/kg"),
        (r"^Garlic \(Native\)", "Local Garlic (Bawang)", "PHP/kg"),
        (r"^Ginger", "Ginger (Luya)", "PHP/kg"),
        (r"^Chill?i", "Chili (Siling labuyo)", "PHP/kg"),
    ],
    "Fruits": [
        (r"^Calamansi", "Calamansi", "PHP/kg"),
        (r"^Banana \(Lakatan\)", "Banana (Lakatan)", "PHP/kg"),
        (r"^Banana \(Latundan\)", "Banana (Latundan)", "PHP/kg"),
        (r"^Papaya", "Papaya", "PHP/kg"),
        (r"^Mango", "Mango (Carabao)", "PHP/kg"),
    ],
    "Other Commodities": [
        (r"^Sugar \(Refined\)", "Sugar (Refined)", "PHP/kg"),
        (r"^Sugar \(Washed\)", "Sugar (Washed)", "PHP/kg"),
        (r"^Sugar \(Brown\)", "Sugar (Brown)", "PHP/kg"),
        (r"^Cooking oil \(Palm\) 350", "Palm Oil - 350ml", "PHP/bottle"),
        (r"^Cooking oil \(Palm\) 1", "Palm Oil - 1L", "PHP/bottle"),
        (r"^Cooking oil \(Coconut\) 350", "Coconut Oil - 350ml", "PHP/bottle"),
        (r"^Cooking oil \(Coconut\) 1", "Coconut Oil - 1L", "PHP/bottle"),
    ],
}
SUMMARY_FIELDS = ["date", "group", "commodity", "unit", "low", "high", "prevailing", "average", "source_file"]


def num(v):
    return "" if v is None or v == "NONE" else float(v.replace(",", ""))


def parse_summary(text, notes):
    group, out = None, []
    for raw in text.splitlines():
        line = raw.strip()
        if line.upper() in GROUPS:
            group = GROUPS[line.upper()]
            continue
        if group is None:
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        label = m.group("label").strip()
        for rx, name, unit in ITEMS[group]:
            if re.match(rx, label, re.I):
                out.append({"group": group, "commodity": name, "unit": unit,
                            "low": num(m.group("lo")), "high": num(m.group("hi")),
                            "prevailing": num(m.group("pv")), "average": ""})
                break
        else:
            if m.group("hi"):  # has numbers but no mapping -> tell the user
                notes.append(f"unmapped row in {group}: '{label}'")
    return out


# ----------------------------------------------------------------------------------------
# 2. PER-MARKET PAGE
# ----------------------------------------------------------------------------------------
# (output commodity name, unit, header keywords)
COLUMNS = [
    ("Well-milled Rice (Local)", "PHP/kg", ["well-milled"]),
    ("Tilapia", "PHP/kg", ["tilapia"]),
    ("Galunggong (Local)", "PHP/kg", ["galunggong"]),
    ("Fresh Whole Chicken", "PHP/kg", ["chicken"]),
    ("Egg (Medium)", "PHP/piece", ["egg", "itlog"]),
    ("Ampalaya", "PHP/kg", ["ampalaya"]),
    ("Tomato", "PHP/kg", ["tomato"]),
    ("Cabbage", "PHP/kg", ["cabbage"]),
    ("Pechay Baguio", "PHP/kg", ["pechay"]),
    ("Red Onion (Local)", "PHP/kg", ["onion"]),
]
MARKET_FIELDS = ["date", "market", "commodity", "unit", "price_low", "price_high", "source_file"]


def clean_word(t):
    return re.sub(r"[()*]", "", t).lower()


