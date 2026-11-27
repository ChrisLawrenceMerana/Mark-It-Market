import argparse
import csv
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    import pdfplumber
    import pdfplumber.page
except ImportError:
    sys.exit("Missing dependency. Run:  pip install pdfplumber")
if not hasattr(pdfplumber.page.Page, "search"):
    sys.exit("pdfplumber is too old. Run:  pip install --upgrade pdfplumber")

NA = r"(?:NOT\s+AVAIL\w*|N/A)"        # used if the PDFs misspell "NOT AVAILBLE"
# A price cell: "45.00", "45.00 - 47.00", "45.00 -47.00", or NOT AVAILABLE
TOKEN = rf"(?:{NA}(?:\s*-\s*{NA})?|\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)"
ROW_RE = re.compile(rf"^(?P<market>.+?)\s+(?P<values>{TOKEN}(?:\s+{TOKEN})*)\s*$")
TOKEN_RE = re.compile(TOKEN)

# Pork / chicken pages: "N/A" = not available in the market, "n/m" = no monitoring done
RANGE = r"\d+(?:\.\d+)?(?:\s*-\s*|\s+)\d+(?:\.\d+)?"   # if the dash is missing
PTOKEN = rf"(?:{RANGE}|(?:N/A|n/m)(?:\s*-\s*(?:N/A|n/m))?)"   # "N/A - N/A" can occur
PROW_RE = re.compile(rf"^(?:\d+\s+)?(?P<market>.+?)\s+(?P<values>{PTOKEN}(?:\s+{PTOKEN})*)\s*$",
                     re.IGNORECASE)
PTOKEN_RE = re.compile(PTOKEN, re.IGNORECASE)

# The PDF writes the date as "Tuesday, December 26, 2023" on some pages and
# "Tuesday, 26 December 2023" on others. Both are accepted.
WEEKDAY = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
DATE_RE = re.compile(
    rf"{WEEKDAY},?\s+(?:(?P<d1>\d{{1,2}})\s+(?P<m1>[A-Za-z]+)\s+(?P<y1>\d{{4}})"
    rf"|(?P<m2>[A-Za-z]+)\s+(?P<d2>\d{{1,2}}),?\s+(?P<y2>\d{{4}}))")
FILE_DATE_RE = re.compile(r"([A-Za-z]+)[-_ ](\d{1,2})[-_ ](\d{4})")

MARKET_PAGE_MARK = "COMMODITY (PHP/KG)"     # the per-market table of selected commodities
PROTEIN_PAGE_MARK = "PRICE PER KILOGRAM"    # the per-market pork and chicken pages
SUMMARY_PAGE_MARK = "at NCR Markets"        # the one-page NCR-wide infographic
MARKET_DIR = "Per Market"                   # output folder names
SUMMARY_DIR = "NCR Summary"

# For parsing
def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def parse_date(raw):
    for f in ("%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def find_date(text):
    m = DATE_RE.search(text)
    if not m:
        return ""
    raw = (f"{m['d1']} {m['m1']} {m['y1']}" if m["d1"]
           else f"{m['m2']} {m['d2']} {m['y2']}")
    return parse_date(raw) or raw


def filename_date(path):
    """'Price-Monitoring-December-26-2023.pdf' -> '2023-12-26' (or '')."""
    m = FILE_DATE_RE.search(Path(path).stem)
    return (parse_date(f"{m[1]} {m[2]} {m[3]}") or "") if m else ""


def fix_line(line):
    """Un-glue cells that the PDF text runs together: 'NOT AVAILABLE110.00 -120.00',
    'NOT AVAILABLENOT AVAILABLE', '0.00n/m0.00'."""
    line = re.sub(r"(?<=[A-Za-z0-9])(?=NOT\s+AVAIL)", " ", line)
    line = re.sub(r"(AVAIL[A-Z]*)(?=\d)", r"\1 ", line)
    line = re.sub(r"(?<=\d)(?=n/m|N/A)", " ", line)
    line = re.sub(r"(?<=n/m)(?=\d)|(?<=N/A)(?=\d)", " ", line)
    return line


def parse_price(cell):
    """'45.00 - 47.00' -> (45.0, 47.0); '45.00' -> (45.0, 45.0); N/A, n/m -> (None, None)."""
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", clean(cell))]
    if not nums:
        return None, None
    return (nums[0], nums[0]) if len(nums) == 1 else (min(nums), max(nums))


# Per-market table of selected commodities (page "... per Market")
def get_commodities(page):
    """Commodity names from the table header (the row starting with MARKET)."""
    for table in page.extract_tables():
        for row in table:
            if row and clean(row[0]).upper() == "MARKET":
                # Merged cells can leave stray empty columns; drop them.
                return [clean(c) for c in row[1:] if clean(c)]
    return None


def parse_market_page(page, text, warn):
    commodities = get_commodities(page)
    if not commodities:
        return None, []
    rows, problems = [], []
    for line in text.splitlines():
        line = clean(fix_line(line))
        if DATE_RE.search(line):
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        cells = [t.group(0) for t in TOKEN_RE.finditer(m.group("values"))]
        if len(cells) != len(commodities):
            problems.append((clean(m.group("market")), len(cells)))
            continue
        rows.append((clean(m.group("market")), cells))
    # Some rows cannot be read from the text lines (a cell that wraps onto two lines, a
    # blank cell). The table reader sees them cell by cell, so use it for those rows.
    have = {market for market, _ in rows}
    recovered = []
    for table in page.extract_tables():
        for row in table:
            market = clean(row[0]) if row else ""
            raw = row[0] if row and row[0] else ""
            if (len(row) - 1 == len(commodities) and raw.startswith("NOT\n")
                    and raw.rstrip().endswith("AVAILABLE") and not any(row[1:])):
                # whole row squeezed into one cell with a wrapped leading NOT AVAILABLE
                body = clean(fix_line(raw.strip()[4:].rstrip()[:-len("AVAILABLE")]))
                m = ROW_RE.match(body)
                if m:
                    vals = [t.group(0) for t in TOKEN_RE.finditer(m.group("values"))]
                    name = clean(m.group("market"))
                    if len(vals) == len(commodities) - 1 and name not in have:
                        rows.append((name, ["NOT AVAILABLE"] + vals))
                        recovered.append(name)
                        continue
            if (not market or market.upper() == "MARKET" or market in have
                    or len(row) - 1 != len(commodities)):
                continue
            cells = [clean(c) for c in row[1:]]
            if any(re.search(r"\d", c) for c in cells):
                rows.append((market, cells))
                recovered.append(market)
    for market, found in problems:
        if not any(market.startswith(r) for r in recovered):
            warn(f"skipped '{market}': expected {len(commodities)} values, found {found}")
    return commodities, rows


# Per-market pork and chicken pages (one commodity group per page)
def parse_protein_page(text, warn):
    """Pages titled e.g. 'FRESH & FROZEN PORK LIEMPO' or 'FRESH WHOLE CHICKEN'.

    Returns ([(commodity, unit), ...], [(market, [cell, ...]), ...]). A page with
    "Fresh" and "Frozen" columns gives two commodities ('Fresh Pork Liempo' and
    'Frozen Pork Liempo'). Markets marked n/m in every column were not monitored and
    are left out.
    """
    lines = [re.sub(r"(?<=[\d/A])\s+[A-Za-z]$", "", clean(fix_line(l)))   # stray glyph at line end
             for l in text.splitlines()]
    heading = ""
    for line in lines:                          # last all-caps line above "PRICE PER KILOGRAM"
        if PROTEIN_PAGE_MARK in line.upper():
            break
        if line.isupper() and not DATE_RE.search(line):
            heading = line
    if not heading:
        warn("pork/chicken page without a recognisable heading, skipped")
        return [], []
    m = re.match(r"(\w+)\s*&\s*(\w+)\s+(.+)$", heading)
    if m:                                       # "FRESH & FROZEN PORK LIEMPO"
        names = [f"{m[1].title()} {m[3].title()}", f"{m[2].title()} {m[3].title()}"]
    else:                                       # "FRESH WHOLE CHICKEN"
        names = [heading.title()]
    rows = []
    for line in lines:
        if DATE_RE.search(line):
            continue
        r = PROW_RE.match(line)
        if not r:
            if re.match(r"^\d+\s+[A-Za-z]", line) and not line.upper().startswith("PRICE"):
                warn(f"skipped unreadable row '{line}'")
            continue
        cells = [t.group(0) for t in PTOKEN_RE.finditer(r.group("values"))]
        if len(cells) != len(names):
            warn(f"skipped '{r.group('market')}': expected {len(names)} "
                 f"values, found {len(cells)}")
            continue
        if all(re.fullmatch(r"n/m(?:\s*-\s*n/m)?", c, re.IGNORECASE) for c in cells):
            continue                            # not monitored that day
        rows.append((clean(r.group("market")), cells))
    return [(n, "PHP/kg") for n in names], rows


# The pork/chicken pages list markets under slightly different names than the main
# table ("Grace Marketplace (Pateros)" vs "Pateros Market"). Their rows are joined to
# the main table's market by name. Names are compared ignoring case, spacing and a
# trailing "/Place" or "(Place)". Anything else that is the same market needs an entry
# here (left side lower case, as printed on the pork/chicken pages). A market that
# cannot be matched is kept under its own name and reported with a warning.
MARKET_ALIASES = {
    "grace marketplace (pateros)": "Pateros Market",
    "mutya ng pasig mega market": "Pasig City Mega Market",
    "mandaluyong public market i": "Mandaluyong Public Market",
    "kamuning market": "Kamuning Public Market",
    "la huerta new parañaque market": "La Huerta Market/Parañaque",
}


def market_key(name):
    return re.sub(r"\s*[/(].*$", "", clean(name)).lower()


def resolve_market(name, known):
    """Map a pork/chicken page market name to the name used in the main table."""
    if clean(name).lower() in {k.lower() for k in known}:
        return next(k for k in known if k.lower() == clean(name).lower())
    target = MARKET_ALIASES.get(clean(name).lower())
    if target:
        return next((k for k in known if k.lower() == target.lower()), target)
    keys = {market_key(k): k for k in known}
    return keys.get(market_key(name))


# NCR-wide summary (one-page infographic: Low - High only)
# One entry per commodity on the infographic: (group, name in the CSV, pattern that
# matches the label printed next to the price). The rice rows are handled separately
# (their group depends on the column). Rows are written in this order. A label that
# matches nothing here is still written (with a blank group) and reported with a
# warning
LIVESTOCK = "Livestock And Poultry Products"
SUMMARY_ITEMS = [
    ("Corn", "Corn (White)", r"^corn \(white\)"),
    ("Corn", "Corn (Yellow)", r"^corn \(yellow\)"),
    (LIVESTOCK, "Beef Rump", r"^beef rump"),
    (LIVESTOCK, "Beef Brisket", r"^beef brisket"),
    (LIVESTOCK, "Pork Kasim", r"^pork kasim"),
    (LIVESTOCK, "Pork Liempo", r"^pork liempo"),
    (LIVESTOCK, "Whole Chicken", r"^whole chicken"),
    (LIVESTOCK, "Egg (Medium)", r"^egg \(medium\)"),
    ("Fish", "Milkfish (Bangus)", r"^milkfish"),
    ("Fish", "Tilapia", r"^tilapia"),
    ("Fish", "Local Round Scad (Galunggong)", r"^local round scad"),
    ("Fish", "Imported Round Scad (Galunggong)", r"^imported round scad"),
    ("Fish", "Alumahan", r"alumahan|indian mackerel"),
    ("Lowland Vegetables", "Bittergourd (Ampalaya)", r"^bittergourd"),
    ("Lowland Vegetables", "String Beans (Sitao)", r"^string beans"),
    ("Lowland Vegetables", "Pechay Tagalog", r"^pechay tagalog"),
    ("Lowland Vegetables", "Squash (Kalabasa)", r"^squash"),
    ("Lowland Vegetables", "Eggplant (Talong)", r"^eggplant"),
    ("Lowland Vegetables", "Tomato (Kamatis)", r"^tomato"),
    ("Highland Vegetables", "Cabbage (Repolyo)", r"^cabbage"),
    ("Highland Vegetables", "Carrot (Karot)", r"^carrot"),
    ("Highland Vegetables", "Baguio Beans (Habitchuelas)", r"^baguio beans"),
    ("Highland Vegetables", "White Potato (Patatas)", r"^white potato"),
    ("Highland Vegetables", "Pechay Baguio", r"^pechay baguio"),
    ("Highland Vegetables", "Chayote (Sayote)", r"^chayote"),
    ("Fruits", "Calamansi", r"^calamansi"),
    ("Fruits", "Banana (Lakatan)", r"^banana \(lakatan\)"),
    ("Fruits", "Banana (Latundan)", r"^banana \(latundan\)"),
    ("Fruits", "Papaya", r"^papaya"),
    ("Fruits", "Mango (Carabao)", r"^mango"),
    ("Other Commodities", "Sugar (Refined)", r"^sugar refined"),
    ("Other Commodities", "Sugar (Washed)", r"^sugar washed"),
    ("Other Commodities", "Sugar (Brown)", r"^sugar brown"),
    ("Other Commodities", "Palm Oil - 350ml", r"^palm oil\s*-\s*350\s*ml"),
    ("Other Commodities", "Palm Oil - 1L", r"^palm oil\s*-\s*1\s*l"),
    ("Other Commodities", "Coconut Oil - 350ml", r"^coconut oil\s*-\s*350\s*ml"),
    ("Other Commodities", "Coconut Oil - 1L", r"^coconut oil\s*-\s*1\s*l"),
    ("Spices", "Local Red Onion (Pulang Sibuyas)", r"^local red onion"),
    ("Spices", "Imported Red Onion (Medium size)", r"^imported red onion"),
    ("Spices", "Local White Onion (Puting Sibuyas)", r"^local white onion"),
    ("Spices", "Imported White Onion (Medium size)", r"^imported white onion"),
    ("Spices", "Imported Garlic (Bawang)", r"^imported garlic"),
    ("Spices", "Local Garlic (Bawang)", r"^local garlic"),
    ("Spices", "Ginger (Luya)", r"^ginger"),
    ("Spices", "Chili (Siling labuyo)", r"^chil|siling labuyo"),
]
# Rice rows: the same four grades are printed under both rice headings.
RICE_GRADES = [
    ("Special", r"^special"),
    ("Premium", r"^premium"),
    ("Well Milled", r"^well-?\s?milled"),
    ("Regular Milled", r"^regular\s?milled"),
]
RICE_GROUPS = [("Imported Commercial Rice", "IMPORTED"), ("Local Commercial Rice", "LOCAL")]
_GROUP_ORDER = [g for g, _ in RICE_GROUPS]

PRICE_CELL = rf"{NA}|\d+\.\d+\s*-\s*\d+\.\d+"   # needs decimals: skips the phone numbers
_LABEL_DROP = {"(per", "kg)", "per", "piece"}


def touching(words, price_x0):
    """The words that form one label with the one nearest the price box (an empty grey
    box next to it must not pull in the neighbouring label)."""
    if not words:
        return words
    first = max(words, key=lambda w: w["x1"])
    group, lo, hi = [first], first["x0"], first["x1"]
    changed = True
    while changed:
        changed = False
        for w in words:
            if w not in group and w["x1"] >= lo - 8 and w["x0"] <= hi + 8:
                group.append(w)
                lo, hi = min(lo, w["x0"]), max(hi, w["x1"])
                changed = True
    return group


def label_text(words):
    """Printed label from the words beside a price (may span two lines)."""
    keep = [w for w in words
            if not (w["text"].isalpha() and w["text"].isupper())   # panel headings (FISH, CORN...)
            and w["text"] not in _LABEL_DROP                       # "(per kg)", "per piece"
            and not re.fullmatch(r"[a-d]", w["text"])              # footnote letters
            and not re.fullmatch(r"-?\d+\.\d+-?", w["text"])]      # a neighbouring price
    keep.sort(key=lambda w: w["top"])
    lines = []
    for w in keep:
        if lines and abs(w["top"] - lines[-1][0]["top"]) < 4:
            lines[-1].append(w)
        else:
            lines.append([w])
    return clean(" ".join(w["text"] for ln in lines
                          for w in sorted(ln, key=lambda w: w["x0"])))


def parse_summary_page(page, warn=lambda msg: None):
    """NCR-wide Low/High rows from the one-page infographic.

    Each price box is paired with the label printed to its left (up to the previous
    price box in the same row). Rows: [group, name, unit, low, high, prevailing, average];
    prevailing and average are None because this layout does not print them.
    """
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
    y_max = next((w["top"] for w in words if w["text"] == "Covered:"), page.height)
    cells = [c for c in page.search(PRICE_CELL, regex=True) if c["bottom"] < y_max]
    mid = lambda o: (o["top"] + o["bottom"]) / 2
    headers = {}                                # x position of the rice headings
    for group, word in RICE_GROUPS:
        for w in words:
            if w["text"] == word:
                headers[group] = w["x0"]

    found = {}                                  # (group, name) -> row
    for c in cells:
        left = max((o["x1"] for o in cells if o is not c and abs(mid(o) - mid(c)) < 12
                    and o["x1"] <= c["x0"]), default=0)
        near = [w for w in words if left - 1 <= w["x0"] and w["x1"] <= c["x0"] + 1
                and abs(mid(w) - mid(c)) <= 11]
        label = label_text(touching(near, c["x0"]))
        if not label:
            continue
        low, high = parse_price(c["text"])      # NOT AVAILABLE -> (None, None)

        group = name = None
        for grade, rx in RICE_GRADES:
            if re.search(rx, label, re.IGNORECASE) and headers:
                cand = [(x, g) for g, x in headers.items() if x <= c["x0"] + 5]
                group, name = (max(cand)[1] if cand else ""), grade
                break
        else:
            for g, n, rx in SUMMARY_ITEMS:
                if re.search(rx, label, re.IGNORECASE):
                    group, name = g, n
                    break
        if name is None:
            group, name = "", label
            warn(f"summary: no group/name rule for label '{label}'")
        if (group, name) in found:
            warn(f"summary: '{name}' found twice, keeping the first")
            continue
        found[(group, name)] = [group, name, unit_for(name), low, high, None, None]

    grades = [n for n, _ in RICE_GRADES]
    known = [(g, n) for g, n, _ in SUMMARY_ITEMS]

    def sort_key(row):                          # imported rice, local rice, then the table order
        if row[0] in _GROUP_ORDER and row[1] in grades:
            return (0, _GROUP_ORDER.index(row[0]), grades.index(row[1]))
        key = (row[0], row[1])
        return (1, known.index(key), 0) if key in known else (2, 0, 0)

    return sorted(found.values(), key=sort_key)


def unit_for(name):
    if re.search(r"\b(350ml|1L)\b", name):
        return "PHP/bottle"
    return "PHP/piece" if re.search(r"\bEgg\b", name) else "PHP/kg"


def convert(pdf_path, warn=lambda msg: None, info=None):
    """Open the PDF once. Returns (iso_date, commodity_order, markets, summary_rows)."""
    markets, order, srows = {}, [], []
    protein = []                                 # joined to `markets` after all pages are read
    report_date = ""
    with pdfplumber.open(pdf_path) as pdf:
        if info is not None:
            info["pages"], info["picture_pages"] = len(pdf.pages), []
        for number, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if not text.strip() and page.images:
                if info is not None:
                    info["picture_pages"].append(number)
                continue                         # a picture of a table: nothing to read, ignored
            if not report_date:
                report_date = find_date(text)
            if "PREVAILING" in text.upper() or "PREVALING" in text.upper():
                warn(f"page {number} looks like the 2025 layout (has a Prevailing column); "
                     "use pdf_to_csv.py for those PDFs. Page skipped")
                continue
            if MARKET_PAGE_MARK in text:
                commodities, rows = parse_market_page(page, text, lambda m: warn(f"page {number}: {m}"))
                if not commodities:
                    warn(f"page {number}: market table found but no header row, skipped")
                    continue
                names = []
                for c in commodities:            # a leading '*' means "per piece"
                    unit = "PHP/piece" if c.startswith("*") else "PHP/kg"
                    names.append((c.lstrip("*").strip(), unit))
                order.extend(n for n in names if n not in order)
                for market, cells in rows:
                    prices = markets.setdefault(market, {})
                    for name, cell in zip(names, cells):
                        prices[name] = parse_price(cell)
            elif PROTEIN_PAGE_MARK in text.upper():
                names, rows = parse_protein_page(text, lambda m: warn(f"page {number}: {m}"))
                order.extend(n for n in names if n not in order)
                protein.append((names, rows))
            elif SUMMARY_PAGE_MARK in text:
                srows.extend(parse_summary_page(page, lambda m: warn(f"page {number}: {m}")))
            elif re.search(r"\d+\.\d\d", text):
                warn(f"page {number}: layout not recognised, skipped")

    known = list(markets)
    unmatched = []
    for names, rows in protein:
        for market, cells in rows:
            target = resolve_market(market, known)
            if target is None:
                if known and market not in unmatched:
                    unmatched.append(market)
                target = market
            prices = markets.setdefault(target, {})
            for name, cell in zip(names, cells):
                prices[name] = parse_price(cell)
    if unmatched:
        warn(f"{len(unmatched)} market(s) on the pork/chicken pages are not in the main "
             "table and were kept under their own names: " + "; ".join(unmatched) +
             ". If one is the same market as a main-table market, add it to MARKET_ALIASES")

    from_name = filename_date(pdf_path)
    if report_date and from_name and report_date != from_name:
        warn(f"date in the PDF ({report_date}) differs from the file name ({from_name}); "
             "using the PDF's")
    if not report_date and from_name:
        warn(f"no date found in the PDF; using the file name's ({from_name})")
        report_date = from_name
    return report_date, order, markets, srows



def unify_market_names(master_m):
    """Give one name to each market across the whole year (combine mode).

    A market can be spelt differently from day to day or page to page ("Agora Public
    Market/San Juan" / "Agora Public Market"). Names are grouped with the same rule as
    resolve_market (case, trailing "/Place" or "(Place)", MARKET_ALIASES); the group
    uses its most common name that has a "/Place" or "(Place)", else its most common name.
    Edits the rows in place. Returns [(final name, original name, rows)] for the report.
    """
    counts = {}
    for _, rows in master_m:
        for r in rows:
            counts[r[1]] = counts.get(r[1], 0) + 1
    groups = {}
    for name in counts:
        alias = MARKET_ALIASES.get(clean(name).lower(), name)
        groups.setdefault(market_key(alias), []).append(name)
    final = {}
    for names in groups.values():
        best = max(names, key=lambda n: (bool(re.search(r"[/(]", n)), counts[n]))
        for n in names:
            final[n] = best
    for _, rows in master_m:
        for r in rows:
            r[1] = final[r[1]]
    return sorted((final[n], n, counts[n]) for n in counts)

# For writing
def fmt(x):
    return "" if x is None else f"{x:.2f}"


def write_wide(path, report_date, order, markets):
    header = ["date", "market"]
    for name, unit in order:
        header += [f"{name} Low ({unit})", f"{name} High ({unit})"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for market, prices in markets.items():
            row = [report_date, market]
            for key in order:
                low, high = prices.get(key, (None, None))
                row += [fmt(low), fmt(high)]
            w.writerow(row)


MARKET_LONG_HEADER = ["date", "market", "commodity", "unit", "price_low", "price_high"]


def market_long_rows(report_date, order, markets):
    for market, prices in markets.items():
        for name, unit in order:
            low, high = prices.get((name, unit), (None, None))
            yield [report_date, market, name, unit, fmt(low), fmt(high)]


SUMMARY_HEADER = ["date", "group", "commodity", "unit", "low", "high",
                  "prevailing", "average"]


def summary_csv_rows(report_date, srows):
    for g, name, unit, low, high, prev, avg in srows:
        yield [report_date, g, name, unit, fmt(low), fmt(high), fmt(prev), fmt(avg)]


def write_rows(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

