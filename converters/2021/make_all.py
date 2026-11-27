"""Build all_ncr_summary.csv and all_markets.csv (same layout as the 2025 files).
Run from pdfs_raw:  py make_all.py"""
import csv, re
def read(p):
    with open(p, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f); return r.fieldnames, list(r)
def pdf(r): r["source_file"] = re.sub(r"\.csv$", ".pdf", r["source_file"], flags=re.I); return r
def write(p, fields, rows):
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

f1, a = read("ncr_summary_2021.csv"); _, b = read("ncr_summary_2021_sepdec_official.csv")
rows = sorted((pdf(r) for r in a + b), key=lambda r: r["date"])   # stable: keeps each day's order
write("all_ncr_summary.csv", f1, rows)
print("all_ncr_summary.csv:", len(rows), "rows,", len({r['date'] for r in rows}), "dates")
f2, m = read("markets_2021.csv")
m = [pdf(r) for r in m]
write("all_markets.csv", f2, m)
print("all_markets.csv:", len(m), "rows,", len({r['date'] for r in m}), "dates")