"""List every PDF with how its page 1 is stored.  Run from pdfs_raw:  py classify_pdfs.py
Writes pdf_types.csv and prints date ranges per type."""
import csv, re, sys, glob
from datetime import date
import pdfplumber
M = {m:i for i,m in enumerate("january february march april may june july august september october november december".split(),1)}
rows = []
for f in sorted(glob.glob(sys.argv[1] if len(sys.argv)>1 else "*.pdf")):
    m = re.search(r"Price-Monitoring-([A-Za-z]+)-(\d{1,2})-(\d{4})", f)
    d = date(int(m.group(3)), M[m.group(1).lower()], int(m.group(2))) if m else None
    try:
        with pdfplumber.open(f) as pdf:
            pg = pdf.pages[0]; txt = pg.extract_text() or ""; words = len(pg.extract_words())
            area = sum(max(0,min(i["x1"],pg.width)-max(i["x0"],0))*max(0,min(i["bottom"],pg.height)-max(i["top"],0)) for i in pg.images)
            cover = area/(pg.width*pg.height)
            if re.search(r"High\s+Low\s+Prevailing", txt): kind = "text table"
            elif words < 150: kind = "image only"
            elif cover > 0.5: kind = "image+text"
            else: kind = "text"
            rows.append([d.isoformat() if d else "", f, kind, words, round(cover,2), len(pdf.pages)])
    except Exception as e:
        rows.append([d.isoformat() if d else "", f, f"error: {e}", "", "", ""])
rows.sort()
with open("pdf_types.csv","w",newline="",encoding="utf-8") as o:
    w = csv.writer(o); w.writerow(["date","file","page1_type","words","image_cover","pages"]); w.writerows(rows)
from itertools import groupby
for kind, g in groupby(sorted(rows,key=lambda r:(r[2],r[0])), key=lambda r:r[2]):
    ds = [r[0] for r in g]; print(f"\n{kind}: {len(ds)} files\n  " + ", ".join(ds))