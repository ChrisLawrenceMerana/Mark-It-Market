"""Make one CSV per date, in two folders.  Run from pdfs_raw:  py split_per_date.py
Reads : ncr_summary_2021.csv, ncr_summary_2021_sepdec_official.csv, markets_2021.csv
Writes: ncr_summary_per_date\\Price-Monitoring-<Month>-<D>-2021.csv
        markets_per_date\\Price-Monitoring-<Month>-<D>-2021.csv
"""
import csv, os, re
from collections import defaultdict

def read(p):
    with open(p, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f); return r.fieldnames, list(r)

