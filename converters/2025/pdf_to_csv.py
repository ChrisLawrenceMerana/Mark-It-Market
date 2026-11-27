#!/usr/bin/env python3
"""
Convert DA "Bantay Presyo" price monitoring PDFs into CSV files.

Outputs go into two folders, created in the current folder (or --outdir):
    Per Market/<name>.csv                  per-market prices (Low/High per commodity)
    NCR Summary/<name>_ncr_summary.csv     NCR-wide Low / High / Prevailing / Average

Drag PDFs onto this file, or from a terminal:
    python pdf_to_csv.py a.pdf b.pdf
    python pdf_to_csv.py --all                  # every PDF in the current folder
    python pdf_to_csv.py input.pdf -o prices.csv   # single file only
    python pdf_to_csv.py input.pdf --long       # one row per market+commodity

Requires:  pip install pdfplumber
"""

import argparse
import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("Missing dependency. Run:  pip install pdfplumber")

NA = "NOT AVAILABLE"
# A price cell: "45.00", "45.00 - 47.00", "45.00 -47.00", or NOT AVAILABLE
TOKEN = rf"(?:{NA}|\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)"
ROW_RE = re.compile(rf"^(?P<market>.+?)\s+(?P<values>{TOKEN}(?:\s+{TOKEN})*)\s*$")
TOKEN_RE = re.compile(TOKEN)
DATE_RE = re.compile(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
                     r"(\d{1,2}\s+\w+\s+\d{4})")
SUM_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<nums>\d+(?:\.\d+)?\**(?:\s*-\s*\d+(?:\.\d+)?\**)?"
    r"(?:\s+\d+(?:\.\d+)?){1,2})(?:\s+[A-Za-z()]{1,8})?$")
MARKET_PAGE_MARK = "COMMODITY (PHP/KG)"
SCAN_FRACTION = 0.6                 # one image covering this much of a page = a scan
MIN_TEXT_CHARS = 20                 # fewer characters than this = page has no text layer
MARKET_DIR = "Per Market"          # output folder names
SUMMARY_DIR = "NCR Summary"


# ----------------------------------------------------------------- parsing
def scan_fraction(page):
    """Share of the page covered by its single biggest image (1.0 = a full-page scan)."""
    area = page.width * page.height
    if not area:
        return 0.0
    best = 0.0
    for im in page.images:
        w = max(0.0, min(im["x1"], page.width) - max(im["x0"], 0.0))
        h = max(0.0, min(im["bottom"], page.height) - max(im["top"], 0.0))
        best = max(best, w * h)
    return best / area


def plausible_market(name):
    """Garbled OCR text produces 'market names' with digits and punctuation."""
    return (3 <= len(name) <= 60 and not re.search(r"\d", name)
            and "NOT AVAIL" not in name.upper())


def plausible_commodity(name):
    return (2 <= len(name) <= 50 and not re.search(r"\d+\.\d{2}", name)
            and "NOT AVAIL" not in name.upper())


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


_MON = (r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
        r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
TEXT_DATE_PATTERNS = [   # (regex, order of the captured groups)
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[ \t]+{_MON}\.?,?[ \t]+(\d{{4}})\b", re.I), "dmy"),
    (re.compile(rf"\b{_MON}\.?[ \t]+(\d{{1,2}})(?:st|nd|rd|th)?,?[ \t]+(\d{{4}})\b", re.I), "mdy"),
]
_MONTH_NUM = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def find_report_date(text):
    """First date in the text, as ISO. Accepts '24 December 2025', 'December 24, 2025',
    abbreviated months, with or without a weekday in front. Returns '' if none."""
    found = []
    for rx, order in TEXT_DATE_PATTERNS:
        for m in rx.finditer(text):
            a, b, c = m.groups()
            mon, day = (b, a) if order == "dmy" else (a, b)
            try:
                d = datetime(int(c), _MONTH_NUM[mon[:3].lower()], int(day))
            except (ValueError, KeyError):
                continue
            found.append((m.start(), d.strftime("%Y-%m-%d")))
    return min(found)[1] if found else ""


def has_date(line):
    """True for the heading line that carries the report date (not a data row)."""
    return bool(DATE_RE.search(line) or find_report_date(line))


def iso_date(raw):
    try:
        return datetime.strptime(raw, "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        return raw


def parse_price(cell):
    """'45.00 - 47.00' -> (45.0, 47.0); '45.00' -> (45.0, 45.0); N/A -> (None, None)."""
    cell = clean(cell)
    if not cell or cell.upper() == NA:
        return None, None
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", cell)]
    return (nums[0], nums[0]) if len(nums) == 1 else (min(nums), max(nums))


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
    rows = []
    for line in text.splitlines():
        line = clean(line)
        if has_date(line):
            continue
        # cells can be glued together without spaces: "...AVAILABLENOT AVAILABLE100.00-160.00"
        line = re.sub(r"(NOT AVAILABLE)(?=\S)", r"\1 ", line)
        line = re.sub(r"(?<=\d)(?=NOT AVAILABLE)", " ", line)
        m = ROW_RE.match(line)
        if not m:
            continue
        cells = [t.group(0) for t in TOKEN_RE.finditer(m.group("values"))]
        if len(cells) != len(commodities):
            warn(f"skipped '{m.group('market')}': expected {len(commodities)} "
                 f"values, found {len(cells)}")
            continue
        market = clean(m.group("market"))
        if not plausible_market(market):
            warn(f"ignored an unreadable row: '{market[:50]}'")
            continue
        rows.append((market, cells))
    return commodities, rows


# The category labels in the PDF are printed sideways beside the tables and do not
# extract reliably, so each commodity is assigned its category by name instead.
# (Rice is handled separately from the real headings in the PDF.) Add new
# commodities here; anything not matched is left blank.
GROUP_RULES = [
    (r"\bCorn\b", "Corn"),
    (r"^(Beef|Pork|Frozen|Whole Chicken|Chicken)", "Livestock And Poultry Products"),
    (r"Milkfish|Bangus|Tilapia|Galunggong|Round Scad|Alumahan|Tamban|Sardines|Squid|Pusit",
     "Fish"),
    (r"Ampalaya|Bittergourd|Sitao|String Beans|Pechay Tagalog|Kalabasa|Squash|Talong|"
     r"Eggplant|Kamatis|Tomato", "Lowland Vegetables"),
    (r"Bell Pepper|Broccoli|Cabbage|Repolyo|Carrot|Baguio Beans|Habitchuelas|Potato|"
     r"Pechay Baguio|Sayote|Chayote|Cauliflower|Celery|Lettuce", "Highland Vegetables"),
    (r"Calamansi|Banana|Papaya|Mango|Avocado|Melon|Pomelo", "Fruits"),
    (r"Sugar|Palm Oil|Coconut Oil", "Other Commodities"),
    (r"Onion|Garlic|Ginger|Luya|Chil", "Spices"),
]


def category_for(name):
    for pattern, group in GROUP_RULES:
        if re.search(pattern, name, re.IGNORECASE):
            return group
    return ""


def clean_name(name):
    name = re.sub(r"\s*[\u1d43-\u1d48]$", "", name)        # superscript footnote letters
    name = re.sub(r"(?<=\))\s*[a-d]$", "", name)           # "(Galunggong)b" / ")c"
    # drop side-label fragments that bleed in ("CORN ", "VESTOCK AND ", "(per kg) ")
    name = re.sub(r"^(?:\(per kg\)|[A-Z]{2,})(?:\s+[A-Z]{2,})*\s+(?=[A-Z][a-z])",
                  "", clean(name))
    return clean(name)


def parse_summary_page(page, warn=lambda msg: None):
    """NCR-wide Low/High/Prevailing/Average rows (two columns per page)."""
    rows, rejected = [], 0
    w, h = page.width, page.height
    for box in ((0, 0, w / 2, h), (w / 2, 0, w, h)):
        group = ""
        for line in (page.crop(box).extract_text() or "").splitlines():
            line = clean(line)
            if line.startswith("COMMODITIES"):
                group = ""                       # a new table begins
                continue
            if line.isupper() and ("COMMERCIAL RICE" in line or "RICE-FOR-ALL" in line):
                group = line.title()
                continue
            m = SUM_RE.match(line)
            if not m or has_date(line):
                continue
            nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", m.group("nums"))]
            if len(nums) == 4:
                low, high, prev, avg = nums
            elif len(nums) == 3:                 # single-value row: low == high
                low, prev, avg = nums
                high = low
            else:
                continue
            name = clean_name(m.group("name"))
            if name.upper().startswith("COMMODITIES"):
                continue
            if not plausible_commodity(name):
                rejected += 1
                continue
            unit = "PHP/piece" if re.search(r"\bEgg\b", name) else "PHP/kg"
            if re.search(r"\b(350ml|1L)\b", name):
                unit = "PHP/bottle"
            rows.append([group or category_for(name), name, unit, low, high, prev, avg])
    if rejected:
        warn(f"{rejected} unreadable summary line(s) ignored")
    return rows


class NoTextError(ValueError):
    """The PDF has no extractable text (e.g. it is a scan / pictures of pages)."""

    def __init__(self, message, info=None):
        super().__init__(message)
        self.info = info or {}


def convert(pdf_path, warn=lambda msg: None, trust_text=False):
    """Open the PDF once. Returns (iso_date, commodity_order, markets, summary_rows)."""
    markets, order, srows = {}, [], []
    report_date = ""
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            raise ValueError("the PDF has no pages")
        total_pages = len(pdf.pages)
        text_pages, empty_pages, image_pages, image_nums = 0, [], 0, []
        scanned_nums, unrecognised = [], []
        for page_no, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            scanned = (not trust_text) and scan_fraction(page) >= SCAN_FRACTION
            if scanned or len(text.strip()) < MIN_TEXT_CHARS:
                empty_pages.append(page_no)
                if page.images:
                    image_pages += 1
                    image_nums.append(page_no)
                if scanned:
                    scanned_nums.append(page_no)  # picture of a page; its text is OCR guesswork
                continue                         # nothing to parse on this page
            text_pages += 1
            if not report_date:
                report_date = find_report_date(text)
            # The NCR summary pages have a "PREVAILING" column; everything else is tried
            # as a per-market page (this also skips the slow table detection on summaries).
            commodities, rows = ((None, []) if "PREVAILING" in text.upper()
                                 else parse_market_page(page, text, warn))
            if not commodities:
                new_rows = parse_summary_page(page, warn)
                if not new_rows:
                    unrecognised.append(page_no)
                    hint = ""
                    if "PREVAILING" not in text.upper() and "NCR MARKETS" in text.upper():
                        hint = (" (it looks like an NCR summary in a different layout, "
                                "without Prevailing/Average columns; not supported yet)")
                    warn(f"page {page_no}: has text but no price table was recognised; "
                         f"skipped{hint}")
                srows.extend(new_rows)
                continue
            names = []
            for c in commodities:                # a leading '*' means "per piece"
                unit = "PHP/piece" if c.startswith("*") else "PHP/kg"
                names.append((c.lstrip("*").strip(), unit))
            order.extend(n for n in names if n not in order)
            for market, cells in rows:
                prices = markets.setdefault(market, {})
                for name, cell in zip(names, cells):
                    prices[name] = parse_price(cell)

    info = {"total_pages": total_pages, "text_pages": text_pages,
            "image_pages": image_pages, "image_page_numbers": image_nums,
            "scanned_pages": scanned_nums, "unrecognised_pages": unrecognised,
            "blank_pages": len(empty_pages) - image_pages}
    if text_pages == 0:
        if scanned_nums:
            raise NoTextError(
                "this PDF is a scan: each page is one picture, and the text inside it is "
                "garbled OCR output that cannot be parsed reliably. It needs the original "
                "text-based PDF, or proper OCR (which this tool does not do)", info)
        if image_pages:
            raise NoTextError(
                "no extractable text: this PDF is made of images (a scan or screenshot), "
                "so there is nothing to read. It needs OCR first (e.g. OCRmyPDF), "
                "which this tool does not do", info)
        raise NoTextError("no extractable text: every page is blank", info)
    if empty_pages:
        pages = ", ".join(map(str, empty_pages))
        kind = ("scanned (image with unreliable OCR text)" if scanned_nums
                else "image-only" if image_pages else "blank")
        warn(f"page(s) {pages} have no usable text ({kind}) and were skipped; "
             f"data on them is missing")
    return report_date, order, markets, srows, info


# ----------------------------------------------------------------- writing
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


# ----------------------------------------------------------------- workers
MONTHS = ("january february march april may june july august september october "
          "november december").split()
_MONTH_RE = "|".join(m[:3] + r"[a-z]*" for m in MONTHS)
FNAME_DATE_PATTERNS = [
    (re.compile(r"(\d{4})[-_.](\d{1,2})[-_.](\d{1,2})"), "ymd_num"),
    (re.compile(rf"({_MONTH_RE})[\s_.,-]*(\d{{1,2}})[\s_.,-]+(\d{{4}})", re.I), "mdy"),
    (re.compile(rf"(\d{{1,2}})[\s_.,-]*({_MONTH_RE})[\s_.,-]*(\d{{4}})", re.I), "dmy"),
]


def filename_date(path):
    """Best-effort date from a file name like 'Price-Monitoring-December-24-2025.pdf'."""
    name = Path(path).stem
    for rx, kind in FNAME_DATE_PATTERNS:
        m = rx.search(name)
        if not m:
            continue
        try:
            if kind == "ymd_num":
                y, mo, d = (int(x) for x in m.groups())
            else:
                a, b, c = m.groups()
                mon, day, y = (a, b, c) if kind == "mdy" else (b, a, c)
                mo = [x[:3] for x in MONTHS].index(mon[:3].lower()) + 1
                d, y = int(day), int(y)
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def resolve_date(pdf, text_date, warnings):
    """Date from the PDF text, else from the file name. Warns if they disagree."""
    fn = filename_date(pdf)
    if text_date and fn and text_date != fn:
        warnings.append(f"report date {text_date} differs from the file name date "
                        f"{fn}; using the report date")
    if text_date:
        return text_date
    if not fn:
        warnings.append("no date found in the PDF text or the file name; "
                        "the date column will be blank")
    return fn


def work(job):
    """Runs in a worker process. Never raises; returns a plain tuple."""
    pdf, trust_text = job
    warnings = []
    try:
        report_date, order, markets, srows, info = convert(pdf, warnings.append, trust_text)
        report_date = resolve_date(pdf, report_date, warnings)
        if not markets and not srows:
            return (pdf, report_date, [], {}, [], warnings,
                    "no price tables found", info)
        return pdf, report_date, order, markets, srows, warnings, None, info
    except NoTextError as e:
        return pdf, "", [], {}, [], warnings, str(e), e.info
    except Exception as e:
        return pdf, "", [], {}, [], warnings, str(e), {}


def pause():
    """Keep the window open when launched by drag-and-drop / double-click."""
    if sys.platform == "win32" and sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to close...")


def find_pdfs(folder, recursive):
    it = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(p for p in it if p.is_file() and p.suffix.lower() == ".pdf")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="*", help="one or more input PDF files")
    ap.add_argument("-o", "--output", help="output CSV (only with a single PDF)")
    ap.add_argument("--all", action="store_true",
                    help="convert every PDF in the current folder")
    ap.add_argument("--outdir", default=".",
                    help="folder in which to create 'Per Market' and 'NCR Summary' "
                         "(default: current folder)")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="with --all, also search subfolders")
    ap.add_argument("--trust-text", action="store_true",
                    help="parse the text of full-page scans anyway (normally skipped as unreliable)")
    ap.add_argument("--no-summary", action="store_true",
                    help="skip the NCR-wide Low/High/Prevailing/Average data")
    ap.add_argument("--long", action="store_true",
                    help="per-file CSV in tidy format: one row per market + commodity")
    args = ap.parse_args()

    if not args.pdfs and not args.all:
        # Double-clicked with nothing dropped: offer to convert the whole folder.
        if sys.stdin and sys.stdin.isatty():
            print("No files given. Drag PDFs onto this script, or convert them all.")
            if input(f"Convert every PDF in {Path.cwd()}? [Y/n] ").strip().lower() in ("", "y", "yes"):
                args.all = True
        if not args.all:
            pause()
            return

    if args.all:
        found = find_pdfs(Path.cwd(), args.recursive)
        args.pdfs = [str(p) for p in found] + args.pdfs
        print(f"Found {len(found)} PDF(s) in {Path.cwd()}"
              f"{' (including subfolders)' if args.recursive else ''}", file=sys.stderr)
    if not args.pdfs:
        print("No PDF files found.")
        pause()
        return
    if args.output and len(args.pdfs) > 1:
        sys.exit("-o/--output can only be used with a single PDF.")

    outdir = Path(args.outdir)
    market_dir, summary_dir = outdir / MARKET_DIR, outdir / SUMMARY_DIR

    pdfs = list(dict.fromkeys(args.pdfs))

    start = time.time()
    done, failed, errors = 0, 0, []

    def handle(res, n):
        nonlocal done, failed
        pdf, report_date, order, markets, srows, warnings, err, info = res
        tag = f"[{n}/{len(pdfs)}] {Path(pdf).name}"
        if err:
            failed += 1
            errors.append((pdf, err))
            print(f"{tag}  FAILED: {err}", file=sys.stderr)
            return
        for w in warnings:
            print(f"{tag}  ! {w}", file=sys.stderr)
        out = Path(args.output) if args.output else market_dir / (Path(pdf).stem + ".csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        if args.long:
            write_rows(out, MARKET_LONG_HEADER,
                       market_long_rows(report_date, order, markets))
        else:
            write_wide(out, report_date, order, markets)
        if srows and not args.no_summary:
            summary_dir.mkdir(parents=True, exist_ok=True)
            sout = summary_dir / (Path(pdf).stem + "_ncr_summary.csv")
            write_rows(sout, SUMMARY_HEADER, summary_csv_rows(report_date, srows))
        done += 1
        print(f"{tag}  ok ({len(markets)} markets, {len(srows)} summary rows)",
              file=sys.stderr)

    for n, pdf in enumerate(pdfs, 1):
        handle(work((pdf, args.trust_text)), n)

    print(f"\nDone: {done} converted, {failed} failed in {time.time() - start:.1f}s.",
          file=sys.stderr)
    pause()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
