"""Build all_ncr_summary.csv and all_markets.csv (same layout as the 2025 files).
Run from pdfs_raw:  py make_all.py"""
import csv, re
def read(p):
    with open(p, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f); return r.fieldnames, list(r)
def pdf(r): r["source_file"] = re.sub(r"\.csv$", ".pdf", r["source_file"], flags=re.I); return r
