import glob
import os
import re
from datetime import datetime
import numpy as np
import pandas as pd
import pdfplumber

GROUP_RULES = [
    (r"\bCorn\b", "Corn"),
    (r"^(Beef|Pork|Frozen|Whole Chicken|Chicken|Egg)", "Livestock And Poultry Products"),
    (r"Milkfish|Bangus|Tilapia|Galunggong|Round Scad|Alumahan|Tamban|Sardines|Squid|Pusit", "Fish"),
    (r"Ampalaya|Bittergourd|Sitao|String Beans|Pechay Tagalog|Kalabasa|Squash|Talong|Eggplant|Kamatis|Tomato", "Lowland Vegetables"),
    (r"Bell Pepper|Broccoli|Cabbage|Repolyo|Carrot|Karot|Baguio Beans|Habitchuelas|Potato|Patatas|Pechay Baguio|Sayote|Chayote|Cauliflower|Celery|Lettuce", "Highland Vegetables"),
    (r"Calamansi|Banana|Papaya|Mango|Avocado|Melon|Pomelo", "Fruits"),
    (r"Sugar|Palm Oil|Coconut Oil", "Other Commodities"),
    (r"Onion|Garlic|Bawang|Ginger|Luya|Chil", "Spices"),
]

CANONICAL_MARKETS = [
    "New Las Piñas City Public Market", "Guadalupe Public Market/Makati", "Guadalupe Public Market",
    "Mandaluyong Public Market", "San Andres Market/Manila", "San Andres Market",
    "Dagonoy Market", "Paco Market", "Sta. Ana Market", "Quinta Market/Manila", "Quinta Market",
    "Blumentritt Market", "Trabajo Market", "Pritil Market/Manila", "Pritil Market",
    "Marikina Public Market", "Concepcion Dos Wet and Dry Market", "Marikina Zone Market",
    "New Parang Market", "Pamilihang Lungsod ng Muntinlupa", "Taguig People's Market",
    "Aling Clara's Wet & Dry Market", "Bicutan Market", "La Huerta New Parañaque Market",
    "Pasay City Market", "Cartimar Market", "Wowee Market", "Pasig City Mega Market",
    "New Arayat Market", "Commonwealth Market/Quezon City", "Commonwealth Market",
    "Litex Wet and Dry Market", "Muñoz Market/Quezon City", "Muñoz Market",
    "Tandang Sora Market", "Visayas Avenue Wet & Dry Market", "Mega Q-mart/Quezon City",
    "Mega Q-mart", "Farmer's Market", "Murphy Public Market", "Agora Public Market",
    "Malabon Central Market", "Da Best Sangandaan Market",
]

TOKEN = r"(?:NOT AVAILABLE|N/A|n/m|\(-\)|\d+(?:,\d{3})*(?:\.\d+)?(?:\s*-\s*\d+(?:,\d{3})*(?:\.\d+)?)?)"
TOKEN_RE = re.compile(TOKEN)

def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def category_for(name):
    for pat, grp in GROUP_RULES:
        if re.search(pat, name, re.IGNORECASE):
            return grp
    return "Other Commodities"

def parse_date_from_filename(fname: str) -> str:
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)-(\d{1,2})-(\d{4})",
        fname, re.IGNORECASE
    )
    if m:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").strftime("%Y-%m-%d")
    return ""

def parse_price_pair(cell_text: str):
    s = clean(cell_text)
    if not s or any(k in s.upper() for k in ["NOT AVAILABLE", "N/A", "N/M", "(-)", "AVAILABLE NOT"]):
        return np.nan, np.nan
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", s)]
    if not nums or all(v == 0.0 for v in nums):
        return np.nan, np.nan
    return (nums[0], nums[0]) if len(nums) == 1 else (min(nums[0], nums[1]), max(nums[0], nums[1]))

def get_unit(commodity: str) -> str:
    c = commodity.lower()
    if "egg" in c:
        return "PHP/piece"
    if "350ml" in c or "1l" in c or "oil" in c:
        return "PHP/bottle"
    return "PHP/kg"

def normalize_commodity(raw_col: str) -> str:
    c = clean(raw_col).replace("*", "")
    cl = c.lower()
    mapping = [
        ("well-milled", "Well-milled Rice (Local)"), ("well milled", "Well-milled Rice (Local)"),
        ("tilapia", "Tilapia"), ("galunggong", "Galunggong"),
        ("whole chicken", "Fresh Whole Chicken"), ("chicken whole", "Fresh Whole Chicken"),
        ("egg", "Egg (Medium)"), ("ampalaya", "Ampalaya"), ("tomato", "Tomato"),
        ("cabbage", "Cabbage"), ("pechay", "Pechay Baguio"), ("baguio pechay", "Pechay Baguio"),
        ("onion", "Red Onion (Local)"), ("sugar", "Sugar (Washed)"),
    ]
    for key, val in mapping:
        if key in cl:
            return val
    return c

def clean_market_name(raw_mkt: str) -> str:
    m = re.sub(r"^\d+\s*", "", clean(raw_mkt))
    if len(m) < 4:
        return ""
    for canon in CANONICAL_MARKETS:
        if m.lower() == canon.lower() or m.lower() in canon.lower() or canon.lower() in m.lower():
            return canon
    return m

def extract_word_lines(page, y_tol=3.5):
    """Groups words on the same y-band into contiguous horizontal text segments."""
    words = page.extract_words(keep_blank_chars=False)
    lines = []
    for w in sorted(words, key=lambda r: (round(r["top"] / y_tol), r["x0"])):
        if lines and abs(w["top"] - lines[-1]["top"]) <= y_tol and (w["x0"] - lines[-1]["x1"]) < 14:
            lines[-1]["txt"] += " " + w["text"]
            lines[-1]["x1"] = max(lines[-1]["x1"], w["x1"])
            lines[-1]["xc"] = (lines[-1]["x0"] + lines[-1]["x1"]) / 2
        else:
            lines.append({
                "x0": w["x0"], "x1": w["x1"],
                "xc": (w["x0"] + w["x1"]) / 2,
                "top": w["top"], "txt": w["text"]
            })
    return lines

def parse_ncr_summary_page(page, date_str: str, source_file: str):
    """Pairs each commodity label with the price range box immediately to its right."""
    records = []
    items = extract_word_lines(page, y_tol=3.5)
    price_re = re.compile(r"^\d+\.\d{2}(?:\s*-\s*\d+\.\d{2})?$")

    # Merge split "150.00" "-" "220.00" segments on the same row
    merged = []
    for it in sorted(items, key=lambda r: (round(r["top"] / 4), r["x0"])):
        if merged and abs(it["top"] - merged[-1]["top"]) <= 4 and (it["x0"] - merged[-1]["x1"]) < 25:
            if re.match(r"^[\d\.\-\s]+$", merged[-1]["txt"]) and re.match(r"^[\d\.\-\s]+$", it["txt"]):
                merged[-1]["txt"] = clean(merged[-1]["txt"] + " " + it["txt"])
                merged[-1]["x1"] = it["x1"]
                continue
        merged.append(it)

    prices = [it for it in merged if price_re.match(it["txt"])]
    labels = [
        it for it in merged
        if not price_re.match(it["txt"])
        and not re.match(r"^[\d\.\-\s]+$", it["txt"])
        and not any(k in it["txt"].upper() for k in ["BANTAY PRESYO", "RETAIL PRICE", "MARKETS COVERED", "SOURCE:", "DISCLAIMER", "ELLIPTICAL", "COMMODIT", "RICE (PER", "FISH (PER", "FRUITS (PER", "VEGETABLES", "LIVESTOCK", "SPICES (PER", "OTHER"])
    ]

    for p in sorted(prices, key=lambda r: (r["top"], r["x0"])):
        cands = [lb for lb in labels if 0 < (p["x0"] - lb["x0"]) < 145 and abs(p["top"] - lb["top"]) <= 12]
        if not cands:
            continue
        best = min(cands, key=lambda c: abs(p["top"] - c["top"]) + (p["x0"] - c["x0"]) * 0.15)
        same_col = sorted([c for c in cands if abs(c["x0"] - best["x0"]) < 25], key=lambda c: c["top"])
        comm = clean(" ".join(c["txt"] for c in same_col)).replace("*", "").strip(" -–")
        if len(comm) < 3 or any(bad in comm.upper() for bad in ["NOT AVAILABLE", "LOC.", "COMPOUND", "IMPORTED COMMERCIAL", "LOCAL COMMERCIAL"]):
            continue

        # Rice columns sit at x0 < 165 (Imported) and 165 <= x0 < 310 (Local)
        if any(r in comm.lower() for r in ["special", "premium", "well-milled", "well milled", "regular milled"]):
            grp = "Imported Commercial Rice" if p["x0"] < 175 else "Local Commercial Rice"
        else:
            grp = category_for(comm)

        low, high = parse_price_pair(p["txt"])
        if not np.isnan(low):
            records.append({
                "date": date_str, "group": grp, "commodity": comm, "unit": get_unit(comm),
                "low": low, "high": high, "prevailing": np.nan, "average": np.nan,
                "source_file": source_file
            })
    return records

def parse_multi_commodity_market_page(page, date_str: str, source_file: str):
    """Aligns every price cell by X-coordinate to its exact column header."""
    records = []
    items = extract_word_lines(page, y_tol=3.5)
    mkt_hdrs = [it for it in items if it["txt"].upper() == "MARKET"]
    if not mkt_hdrs:
        return records
    hdr_y = mkt_hdrs[0]["top"]

    hdr_items = [
        it for it in items
        if abs(it["top"] - hdr_y) <= 20 and it["x0"] > 175
        and "COMMODITY" not in it["txt"].upper() and "PHP" not in it["txt"].upper()
    ]
    cols = []
    for h in sorted(hdr_items, key=lambda r: (r["xc"], r["top"])):
        if cols and abs(h["xc"] - cols[-1]["xc"]) < 24:
            cols[-1]["txt"] += " " + h["txt"]
            cols[-1]["xc"] = (cols[-1]["xc"] + h["xc"]) / 2
        else:
            cols.append({"xc": h["xc"], "txt": h["txt"]})
    for c in cols:
        c["comm"] = normalize_commodity(c["txt"])
    if not cols:
        return records

    mkts = sorted([
        it for it in items
        if it["x0"] < 175 and it["top"] > hdr_y + 15
        and not any(k in it["txt"].upper() for k in ["SOURCE", "NOTE", "PRICE PER", "ELLIPTICAL", "COMPOUND", "926-8203"])
    ], key=lambda r: r["top"])

    vals = [
        it for it in items
        if it["x0"] > 175 and it["top"] > hdr_y + 15
        and not any(k in it["txt"].upper() for k in ["SOURCE", "NOTE", "ELLIPTICAL", "COMPOUND"])
    ]

    for i, m in enumerate(mkts):
        mkt = clean_market_name(m["txt"])
        if not mkt:
            continue
        y_lo = m["top"] - 11
        y_hi = (mkts[i + 1]["top"] - 11) if i + 1 < len(mkts) else m["top"] + 25
        row_vals = [v for v in vals if y_lo <= v["top"] < y_hi]

        assigned = {c["comm"]: "" for c in cols}
        for v in row_vals:
            best_col = min(cols, key=lambda c: abs(c["xc"] - v["xc"]))
            if abs(best_col["xc"] - v["xc"]) < 42:
                assigned[best_col["comm"]] = clean(assigned[best_col["comm"]] + " " + v["txt"])

        for c in cols:
            comm = c["comm"]
            low, high = parse_price_pair(assigned[comm])
            records.append({
                "date": date_str, "market": mkt, "commodity": comm,
                "unit": get_unit(comm), "price_low": low, "price_high": high,
                "source_file": source_file
            })
    return records

def parse_pork_chicken_page(page, header_upper: str, date_str: str, source_file: str):
    records = []
    if "KASIM" in header_upper:
        base_comm = "Pork Kasim/Pigue"
    elif "LIEMPO" in header_upper:
        base_comm = "Pork Liempo"
    elif "WHOLE CHICKEN" in header_upper:
        base_comm = "Fresh Whole Chicken"
    else:
        return records

    text = page.extract_text() or ""
    for raw_line in text.splitlines():
        line = clean(raw_line)
        m = re.match(r"^(?P<num>\d{1,2})\s+(?P<rest>.+)$", line)
        if not m:
            continue
        rest = m.group("rest")
        split_m = re.search(r"\s+(?=(?:\d+\.\d{2}|N/A|n/m|\(-\)))", rest)
        if not split_m:
            continue
        mkt = clean_market_name(rest[:split_m.start()])
        vals_str = rest[split_m.start():].strip()
        if not mkt:
            continue
        tokens = [t.group(0) for t in TOKEN_RE.finditer(vals_str)]
        if not tokens:
            continue

        if base_comm == "Fresh Whole Chicken":
            low, high = parse_price_pair(tokens[0])
            records.append({
                "date": date_str, "market": mkt, "commodity": "Fresh Whole Chicken",
                "unit": "PHP/kg", "price_low": low, "price_high": high, "source_file": source_file
            })
        else:
            fresh_tok = tokens[0]
            frozen_tok = tokens[2] if ("TODAY" in header_upper and len(tokens) >= 3) else (tokens[1] if len(tokens) >= 2 else "")
            for prefix, tok in [("Fresh", fresh_tok), ("Frozen", frozen_tok)]:
                low, high = parse_price_pair(tok)
                records.append({
                    "date": date_str, "market": mkt, "commodity": f"{prefix} {base_comm}",
                    "unit": "PHP/kg", "price_low": low, "price_high": high, "source_file": source_file
                })
    return records

pdf_files = sorted(glob.glob("pdf_2022/*.pdf"))
print(f"Re-parsing {len(pdf_files)} PDFs with coordinate alignment...")

all_markets_rows, all_ncr_rows = [], []
for i, pdf_path in enumerate(pdf_files, start=1):
    fname = os.path.basename(pdf_path)
    date_str = parse_date_from_filename(fname)
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            raw_text = page.extract_text() or ""
            header_upper = " ".join(raw_text.splitlines()[:12]).upper()
            if "SITUATION REPORT" in header_upper or "NUMBER OF STALLS" in header_upper:
                continue
            if "AT NCR MARKETS" in header_upper:
                all_ncr_rows.extend(parse_ncr_summary_page(page, date_str, fname))
            elif "COMMODITIES PER MARKET" in header_upper:
                all_markets_rows.extend(parse_multi_commodity_market_page(page, date_str, fname))
            else:
                all_markets_rows.extend(parse_pork_chicken_page(page, header_upper, date_str, fname))
    if i % 50 == 0 or i == len(pdf_files):
        print(f"[{i}/{len(pdf_files)}] Processed {fname}")

df_markets = (
    pd.DataFrame(all_markets_rows, columns=["date", "market", "commodity", "unit", "price_low", "price_high", "source_file"])
    .drop_duplicates(subset=["date", "market", "commodity", "source_file"])
    .sort_values(["date", "market", "commodity"])
)
df_ncr = (
    pd.DataFrame(all_ncr_rows, columns=["date", "group", "commodity", "unit", "low", "high", "prevailing", "average", "source_file"])
    .drop_duplicates(subset=["date", "group", "commodity", "source_file"])
    .sort_values(["date", "group", "commodity"])
)

df_markets.to_csv("all_markets_2022.csv", index=False, encoding="utf-8-sig")
df_ncr.to_csv("all_ncr_summary_2022.csv", index=False, encoding="utf-8-sig")

print(f"\nSaved all_markets_2022.csv: {len(df_markets):,} rows across {df_markets['date'].nunique()} dates")
print(df_markets["commodity"].value_counts().to_string())
print(f"\nSaved all_ncr_summary_2022.csv: {len(df_ncr):,} rows across {df_ncr['date'].nunique()} dates")
print(df_ncr["group"].value_counts().to_string())