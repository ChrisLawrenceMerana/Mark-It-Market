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


def normalise_market(label):
    label = re.sub(r"\s+", " ", label).strip()
    return re.sub(r"\s*\(([^)]+)\)$", r"/\1", label)  # "X (Makati)" -> "X/Makati"


def parse_market_page(page, notes):
    words = page.extract_words()
    if len(words) < 20:
        notes.append("per-market page has no text layer")
        return None
    foot = [w["top"] for w in words if w["text"].lower().startswith("source")]
    foot_top = min(foot) if foot else page.height
    words = [w for w in words if w["top"] < foot_top]
    for w in words:
        w["xc"] = (w["x0"] + w["x1"]) / 2
        w["yc"] = (w["top"] + w["bottom"]) / 2

    data = [w for w in words if PRICE_RE.match(w["text"]) or w["text"] in ("N/A", "NOT")]
    if not data:
        notes.append("no price tokens found on the per-market page")
        return None
    # drop stray tiny text (hidden leftovers) that is much smaller than the real prices
    med_h = statistics.median([w["bottom"] - w["top"] for w in data])
    data = [w for w in data if (w["bottom"] - w["top"]) >= 0.6 * med_h]
    first_data_top = min(w["top"] for w in data)
    header = [w for w in words if w["top"] < first_data_top]

    centres = []
    for name, unit, keys in COLUMNS:
        hits = [w["xc"] for w in header if any(k in clean_word(w["text"]) for k in keys)]
        centres.append(statistics.mean(hits) if hits else None)
    missing = [COLUMNS[i][0] for i, c in enumerate(centres) if c is None]
    if missing:
        notes.append(f"header columns not found: {missing}")
        return None
    spacing = statistics.median([b - a for a, b in zip(sorted(centres), sorted(centres)[1:])])
    boundary = min(centres) - spacing / 2

    header_bottom = max(w["bottom"] for w in header if any(
        k in clean_word(w["text"]) for _, _, keys in COLUMNS for k in keys))
    big = set(id(w) for w in data)
    label_words = [w for w in words if id(w) not in big and w["xc"] < boundary
                   and w["top"] > header_bottom - 1
                   and (w["bottom"] - w["top"]) >= 0.5 * med_h]
    if not label_words:
        notes.append("no market labels found")
        return None
    tol = 0.5 * statistics.median([w["bottom"] - w["top"] for w in label_words])
    rows = []
    for w in sorted(label_words, key=lambda w: w["yc"]):
        if rows and abs(w["yc"] - rows[-1]["yc"]) <= tol * 2:
            rows[-1]["words"].append(w)
            rows[-1]["yc"] = statistics.mean(x["yc"] for x in rows[-1]["words"])
        else:
            rows.append({"words": [w], "yc": w["yc"]})
    for r in rows:
        r["label"] = normalise_market(" ".join(
            x["text"] for x in sorted(r["words"], key=lambda x: x["x0"])))
        r["vals"] = {}

    for w in data:
        row = min(rows, key=lambda r: abs(r["yc"] - w["yc"]))
        col = min(range(len(centres)), key=lambda i: abs(centres[i] - w["xc"]))
        if abs(centres[col] - w["xc"]) > spacing * 0.6:
            notes.append(f"price {w['text']} far from any column near '{row['label']}' - ignored")
            continue
        if col in row["vals"]:
            notes.append(f"two values in one cell: {row['label']} / {COLUMNS[col][0]}")
        row["vals"][col] = None if w["text"] in ("N/A", "NOT") else float(w["text"].replace(",", ""))

    for r in rows:
        got = len(r["vals"])
        if 0 < got < len(COLUMNS):
            notes.append(f"{r['label']}: only {got}/{len(COLUMNS)} cells found")
    return rows


# ----------------------------------------------------------------------------------------
def date_from_name(name):
    m = FNAME_RE.search(name)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))


def write_csv(path, fields, rows, key_fields):
    seen, final = set(), []
    for r in sorted(rows, key=lambda r: tuple(str(r[k]) for k in key_fields)):
        key = tuple(r[k] for k in key_fields)
        if key not in seen:
            seen.add(key)
            final.append(r)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(final)
    return len(final)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=".")
    ap.add_argument("--extra", nargs="*", default=[], help="extra per-market CSVs to merge in")
    args = ap.parse_args()

    by_date = {}
    for p in sorted(Path(args.folder).glob("*.pdf")):
        d = date_from_name(p.name)
        if d is None:
            print(f"skip (no date in filename): {p.name}")
            continue
        if d not in by_date or "revised" in p.name.lower():  # prefer the "-revised" file
            by_date[d] = p

    summary_rows, market_rows = [], []
    cat = {"needs_ocr": [], "infographic": [], "problems": [], "warnings": []}
    n_sum = n_mkt = n_sumonly = 0

    for d, p in sorted(by_date.items()):
        notes_s, notes_m = [], []
        try:
            with pdfplumber.open(p) as pdf:
                pages = pdf.pages
                texts = [pg.extract_text() or "" for pg in pages]

                # ---- NCR summary (tabled format)
                tabled = next((i for i, t in enumerate(texts)
                               if re.search(r"High\s+Low\s+Prevailing", t)), None)
                got_summary = False
                if tabled is not None:
                    rows = parse_summary(texts[tabled], notes_s)
                    if rows:
                        got_summary = True
                        n_sum += 1
                        for r in rows:
                            summary_rows.append({"date": d.isoformat(), **r, "source_file": p.stem + ".csv"})
                    else:
                        cat["problems"].append(f"{p.name}: summary table found but no rows read")
                else:
                    cat["infographic"].append(p.name)

                # ---- per-market page
                idx = next((i for i, t in enumerate(texts)
                            if re.search(r"per\s+market", t, re.I)), None)
                if idx is None:
                    if got_summary:
                        n_sumonly += 1
                    else:
                        blank = [i + 1 for i, pg in enumerate(pages) if len(pg.extract_words()) < 20]
                        if blank:
                            cat["needs_ocr"].append(f"{p.name}: per-market table is an image (page {blank})")
                        else:
                            cat["problems"].append(f"{p.name}: no per-market page found")
                else:
                    rows = parse_market_page(pages[idx], notes_m)
                    if rows is None:
                        cat["problems"].append(f"{p.name}: per-market page not parsed - " + "; ".join(notes_m))
                    else:
                        n_mkt += 1
                        for r in rows:
                            for i, (name, unit, _) in enumerate(COLUMNS):
                                v = r["vals"].get(i)
                                market_rows.append({
                                    "date": d.isoformat(), "market": r["label"], "commodity": name,
                                    "unit": unit, "price_low": "" if v is None else v,
                                    "price_high": "" if v is None else v, "source_file": p.stem + ".csv"})
        except Exception as e:  # noqa: BLE001
            cat["problems"].append(f"{p.name}: could not read PDF ({e})")
            print(f"[x] {p.name}")
            continue
        warn = notes_s + notes_m
        if warn:
            cat["warnings"].append(f"{p.name}: " + "; ".join(warn))
        print(f"[ok] {p.name}")

    for path in args.extra:
        with open(path, newline="", encoding="utf-8") as f:
            market_rows.extend(csv.DictReader(f))

    ns = write_csv("ncr_summary_2021.csv", SUMMARY_FIELDS, summary_rows, ["date", "group", "commodity"])
    nm = write_csv("markets_2021.csv", MARKET_FIELDS, market_rows, ["date", "market", "commodity"])

    lines = [
        f"NCR summary parsed from {n_sum} files ({ns} rows) -> ncr_summary_2021.csv",
        f"Per-market parsed from {n_mkt} files (+ any --extra) ({nm} rows) -> markets_2021.csv",
        f"{n_sumonly} files are summary-only (no per-market table exists in them: expected for early 2021)",
        "",
        f"== NEEDS OCR / MANUAL ENTRY ({len(cat['needs_ocr'])}): per-market table is a picture ==",
        *cat["needs_ocr"], "",
        f"== SUMMARY IS AN INFOGRAPHIC, NOT PARSED ({len(cat['infographic'])}): only today's prevailing price, no low/high ==",
        *cat["infographic"], "",
        f"== PROBLEMS ({len(cat['problems'])}) ==", *cat["problems"], "",
        f"== WARNINGS ({len(cat['warnings'])}) ==", *cat["warnings"],
    ]
    Path("parse_report.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines[:3]))
    print(f"Needs OCR: {len(cat['needs_ocr'])} | Infographic summary: {len(cat['infographic'])} | "
          f"Problems: {len(cat['problems'])} | Warnings: {len(cat['warnings'])}  (see parse_report.txt)")


if __name__ == "__main__":
    sys.exit(main())