"""
Read the Sep - Dec 2021 "Prevailing Retail Prices ... at NCR Markets" infographic (page 1 of each PDF)
into the NCR-summary CSV layout:

    date, group, commodity, unit, low, high, prevailing, average, source_file

Only TODAY's prevailing price exists in these pages, so low / high / average stay blank.

How it works
  * Words come from the PDF text layer when there is one (most yellow-box files), otherwise from
    OCR (Tesseract) on the rendered page (green-design files and image-only files).
  * Every commodity box is a pair of numbers (today, yesterday). The pairs are grouped into columns
    by position and named by their fixed place in the layout, then checked: a page is only accepted
    when it has exactly 51 pairs in the expected column sizes. Anything else is listed for manual review.
  * Today's value on day D should equal the "yesterday" value printed on the next working day. Mismatches
    are listed in infographic_report.txt so you can spot-check likely OCR mistakes.

Needs:  py -m pip install pytesseract   and the Tesseract program installed on Windows.

Usage (from pdfs_raw):
    py ..\\daily_pdf_to_csv-main\\parse_2021_infographic.py . --extra ..\\daily_pdf_to_csv-main\\manual_oct7_oct8_summary.csv
Output: ncr_summary_2021_sepdec_official.csv  and  infographic_report.txt
"""
import csv
import os
import re
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
FNAME_RE = re.compile(r"Price-Monitoring-([A-Za-z]+)-(\d{1,2})-(\d{4})", re.I)
PRICE = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2}$")
NAWORD = re.compile(r"^(N/?A\.?|NIA)$", re.I)
RENDER_WIDTH = 3200
FIELDS = ["date", "group", "commodity", "unit", "low", "high", "prevailing", "average", "source_file"]

# ---- commodity tables: (group, name, unit) -------------------------------------------------
def R(group, label):
    return (group, label, "PHP/kg")

IMP_RICE = [R("Imported Commercial Rice", n) for n in ("Special", "Premium", "Well Milled", "Regular Milled")]
LOC_RICE = [R("Local Commercial Rice", n) for n in ("Special", "Premium", "Well Milled", "Regular Milled")]
SPICES_ALL = [
    R("Spices", "Local Red Onion (Pulang Sibuyas)"), R("Spices", "Imported Red Onion"),
    R("Spices", "Local White Onion (Puting Sibuyas)"), R("Spices", "Imported White Onion"),
    R("Spices", "Local Garlic (Bawang)"), R("Spices", "Imported Garlic (Bawang)"),
    R("Spices", "Ginger (Luya)"), R("Spices", "Chili (Siling labuyo)")]
LOWLAND = [R("Lowland Vegetables", n) for n in (
    "Bittergourd (Ampalaya)", "String Beans (Sitao)", "Pechay Tagalog",
    "Squash (Kalabasa)", "Eggplant (Talong)", "Tomato (Kamatis)")]
HIGHLAND = [R("Highland Vegetables", n) for n in (
    "Cabbage Repolyo (Scorpio)", "Carrot (Karot)", "Baguio Beans (Habitchuelas)",
    "White Potato (Patatas)", "Pechay Baguio", "Chayote (Sayote)")]
LIVESTOCK = [R("Livestock And Poultry Products", n) for n in (
    "Beef Rump", "Beef Brisket", "Pork Ham", "Pork Belly", "Whole Chicken")] + [
    ("Livestock And Poultry Products", "Chicken Egg (Medium)", "PHP/piece")]
FISH = [R("Fish", n) for n in (
    "Milkfish (Bangus)", "Tilapia", "Local Round Scad (Galunggong)",
    "Imported Round Scad (Galunggong)", "Alumahan")]
FRUITS = [R("Fruits", n) for n in (
    "Calamansi", "Banana (Lakatan)", "Banana (Latundan)", "Papaya", "Mango (Carabao)")]
OTHER = [R("Other Commodities", n) for n in ("Sugar (Refined)", "Sugar (Washed)", "Sugar (Brown)")] + [
    ("Other Commodities", "Palm Oil - 350ml", "PHP/bottle"), ("Other Commodities", "Palm Oil - 1L", "PHP/bottle"),
    ("Other Commodities", "Coconut Oil - 350ml", "PHP/bottle"), ("Other Commodities", "Coconut Oil - 1L", "PHP/bottle")]

# Green design (Nov-Dec): 4 columns, each = top block then bottom block (top to bottom)
GREEN = [
    SPICES_ALL + [(g, n, u) for (g, n, u) in
                  [(("Local Commercial Rice" if i % 2 == 0 else "Imported Commercial Rice"), nm, "PHP/kg")
                   for nm in ("Special", "Premium", "Well Milled", "Regular Milled") for i in (0, 1)]],
    LOWLAND + FRUITS,
    HIGHLAND + OTHER,
    LIVESTOCK + FISH,
]
# Yellow-box design (Sep-Oct): 5 columns
YELLOW = [
    IMP_RICE + LIVESTOCK,
    LOC_RICE + HIGHLAND,
    FISH + LOWLAND,
    FRUITS + [SPICES_ALL[0], SPICES_ALL[1], SPICES_ALL[2], SPICES_ALL[3]],
    OTHER + [SPICES_ALL[5], SPICES_ALL[4], SPICES_ALL[6], SPICES_ALL[7]],   # Imported garlic sits above Local garlic
]
LAYOUTS = {"green": GREEN, "yellow": YELLOW}


