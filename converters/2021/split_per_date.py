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

def split(rows, fields, folder):
    os.makedirs(folder, exist_ok=True)
    groups = defaultdict(list)
    for r in rows:
        stem = re.sub(r"\.(csv|pdf)$", "", r["source_file"], flags=re.I)
        groups[stem].append(r)
    for stem, rs in groups.items():
        with open(os.path.join(folder, stem + ".csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rs)
    return len(groups)

f1, a = read("ncr_summary_2021.csv")
_,  b = read("ncr_summary_2021_sepdec_official.csv")
print("ncr_summary_per_date:", split(a + b, f1, "ncr_summary_per_date"), "files")
f2, m = read("markets_2021.csv")
print("markets_per_date:", split(m, f2, "markets_per_date"), "files")