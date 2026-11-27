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
