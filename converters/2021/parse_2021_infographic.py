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


# ---- words: text layer or OCR ----------------------------------------------------------------
def find_tesseract():
    import pytesseract
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            break
    return pytesseract


def text_words(page):
    s = RENDER_WIDTH / float(page.width)
    return [dict(text=w["text"], x0=w["x0"] * s, x1=w["x1"] * s, top=w["top"] * s, bottom=w["bottom"] * s)
            for w in page.extract_words()]


def ocr_variants(page):
    """Yield word lists from several OCR settings, best-first."""
    from PIL import ImageOps
    pt = find_tesseract()
    for width in (RENDER_WIDTH, 4200):
        im = page.to_image(resolution=72 * width / float(page.width)).original.convert("RGB")
        scale = RENDER_WIDTH / width
        gray = ImageOps.grayscale(im)
        for img, cfg in ((im, "--psm 11"), (gray, "--psm 11"), (gray, "--psm 12"),
                         (gray.point(lambda p: 255 if p > 120 else 0), "--psm 11")):
            d = pt.image_to_data(img, config=cfg, output_type=pt.Output.DICT)
            words = []
            for i, t in enumerate(d["text"]):
                t = t.strip()
                if t and float(d["conf"][i]) >= 0:
                    words.append(dict(text=t, x0=d["left"][i] * scale, x1=(d["left"][i] + d["width"][i]) * scale,
                                      top=d["top"][i] * scale, bottom=(d["top"][i] + d["height"][i]) * scale))
            yield words


# ---- pairing ----------------------------------------------------------------------------------
def to_values(words):
    vals = []
    for w in words:
        t = w["text"]
        if PRICE.match(t):
            v = float(t.replace(",", ""))
        elif NAWORD.match(t) or t.upper() == "NOT":
            v = None
        else:
            continue
        w = dict(w, val=v, yc=(w["top"] + w["bottom"]) / 2, h=w["bottom"] - w["top"])
        vals.append(w)
    return vals


def make_pairs(vals):
    prices = [w for w in vals if w["val"] is not None]
    if not prices:
        return []
    pw = statistics.median([w["x1"] - w["x0"] for w in prices])
    vals = sorted(vals, key=lambda w: w["x0"])
    used, pairs = set(), []
    for i, a in enumerate(vals):
        if i in used:
            continue
        best = None
        for j, b in enumerate(vals):
            if j == i or j in used:
                continue
            if abs(a["yc"] - b["yc"]) < 0.6 * max(a["h"], b["h"]) and 0 < b["x0"] - a["x1"] < 2.5 * pw:
                if best is None or b["x0"] < vals[best]["x0"]:
                    best = j
        if best is not None:
            used.update((i, best))
            pairs.append((a, vals[best]))
    return pairs


def assign(pairs):
    """Return (layout_name, [(group,name,unit,today,yesterday)...]) or (None, reason)."""
    if len(pairs) != 51:
        return None, f"found {len(pairs)} number pairs (expected 51)"
    pairs = sorted(pairs, key=lambda p: p[0]["x0"])
    cols, cur = [], [pairs[0]]
    for p in pairs[1:]:
        if p[0]["x0"] - cur[-1][0]["x0"] > 0.045 * RENDER_WIDTH:
            cols.append(cur)
            cur = [p]
        else:
            cur.append(p)
    cols.append(cur)
    sizes = [len(c) for c in cols]
    for name, layout in LAYOUTS.items():
        if sizes == [len(c) for c in layout]:
            out = []
            for col, items in zip(cols, layout):
                col = sorted(col, key=lambda p: p[0]["yc"])
                for p, (g, n, u) in zip(col, items):
                    out.append((g, n, u, p[0]["val"], p[1]["val"]))
            return name, out
    return None, f"column sizes {sizes} match no known layout"


def read_page(page):
    words = text_words(page)
    sources = []
    if len(words) >= 150:
        sources.append(("text", iter([words])))
    else:
        sources.append(("ocr", ocr_variants(page)))
    why = "no reading attempted"
    for src, it in sources:
        for w in it:
            layout, res = assign(make_pairs(to_values(w)))
            if layout:
                return src, layout, res, ""
            why = res
    return None, None, None, why


# ---- main -------------------------------------------------------------------------------------
def date_from_name(name):
    m = FNAME_RE.search(name)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))


def main(folder=".", extras=()):
    by_date = {}
    for p in sorted(Path(folder).glob("*.pdf")):
        d = date_from_name(p.name)
        if d and (d not in by_date or "revised" in p.name.lower()):
            by_date[d] = p

    rows, failed, results = [], [], {}
    files, layouts = {}, {}
    for d, p in sorted(by_date.items()):
        try:
            with pdfplumber.open(p) as pdf:
                page = pdf.pages[0]
                if re.search(r"High\s+Low\s+Prevailing", page.extract_text() or ""):
                    continue            # tabled months are handled by parse_2021.py
                src, layout, res, why = read_page(page)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{p.name}: could not read ({e})")
            print(f"[x] {p.name}")
            continue
        if layout is None:
            failed.append(f"{p.name}: {why}")
            print(f"[x] {p.name}: {why}")
            continue
        results[d] = res
        files[d], layouts[d] = p.name, (layout, src)
        for g, n, u, today, _ in res:
            rows.append({"date": d.isoformat(), "group": g, "commodity": n, "unit": u, "low": "", "high": "",
                         "prevailing": "" if today is None else today, "average": "",
                         "source_file": p.stem + ".csv"})
        print(f"[ok] {p.name} ({layout}, {src})")

    # consistency check: today's value on day D vs the "yesterday" printed on the next file (matched by name)
    mism = []
    ds = sorted(results)
    for a, b in zip(ds, ds[1:]):
        if (b - a).days > 4:
            continue
        today_a = {(g, n): t for g, n, u, t, y in results[a]}
        for g, n, u, t_b, y_b in results[b]:
            if (g, n) in today_a and today_a[(g, n)] != y_b:
                mism.append(f"{a} today={today_a[(g, n)]} vs {b} yesterday={y_b}: {n}   [{files[a]} / {files[b]}]")

    for path in extras:                       # hand-entered days (image-only pages), same columns
        with open(path, newline="", encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    seen, final = set(), []
    for r in sorted(rows, key=lambda r: (r["date"], r["group"], r["commodity"])):
        key = (r["date"], r["group"], r["commodity"])
        if key not in seen:
            seen.add(key)
            final.append(r)
    with open("ncr_summary_2021_sepdec_official.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(final)
    from collections import Counter
    kinds = Counter(f"{lay}/{src}" for lay, src in layouts.values())
    lines = [f"Read {len(results)} infographic pages ({len(rows)} rows); {len(failed)} could not be read.",
             "Layouts read: " + ", ".join(f"{k}: {v}" for k, v in sorted(kinds.items())),
             f"{len(mism)} day-to-day mismatches (today's price vs next day's 'yesterday') - spot-check these;",
             "they are either genuine revisions or OCR mistakes.", "",
             f"== COULD NOT BE READ ({len(failed)}) =="] + failed + ["", f"== MISMATCHES ({len(mism)}) =="] + mism
    Path("infographic_report.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines[:3]))


if __name__ == "__main__":
    args = sys.argv[1:]
    folder = args[0] if args and not args[0].startswith("--") else "."
    extras = args[args.index("--extra") + 1:] if "--extra" in args else []
    sys.exit(main(folder, extras))