"""
parser.py

Extracts from DA Bantay Presyo PDFs:
1. Per-market price grid -> parse_market_page()
   Columns: date, market, city, commodity, unit, price_min, price_max, available
2. Daily NCR summary -> parse_daily_summary()
   Columns: date, group, commodity, unit, low, high, prevailing, average,
            market_count, prevailing_source, average_source

Inspect a file from the terminal:
    python parser.py "path/to/report.pdf"
"""

from __future__ import annotations

import bisect
import math
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber

# Settings

EXPECTED_MARKETS: int | None = None
MIN_READABLE_CHARS = 15

MARKET_COLUMNS = [
    "date",
    "market",
    "city",
    "commodity",
    "unit",
    "price_min",
    "price_max",
    "available",
]

SUMMARY_COLUMNS = [
    "date",
    "group",
    "commodity",
    "unit",
    "low",
    "high",
    "prevailing",
    "average",
    "market_count",
    "prevailing_source",
    "average_source",
]

# (clean name, unit, header keywords, summary group)
COMMODITIES = [
    ("Well-milled Rice (Local)", "PHP/kg", ("rice",), "Local Commercial Rice"),
    ("Corn (White)", "PHP/kg", ("corn",), "Corn"),
    ("Corn (Yellow)", "PHP/kg", ("corn",), "Corn"),
    ("Tilapia", "PHP/kg", ("tilapia",), "Fish"),
    ("Galunggong", "PHP/kg", ("galungg",), "Fish"),
    ("Egg (Medium)", "PHP/piece", ("egg",), "Livestock And Poultry Products"),
    ("Ampalaya", "PHP/kg", ("ampalaya",), "Lowland Vegetables"),
    ("Tomato", "PHP/kg", ("tomato",), "Lowland Vegetables"),
    ("Cabbage (Rareball)", "PHP/kg", ("rare",), "Highland Vegetables"),
    ("Cabbage (Scorpio)", "PHP/kg", ("scorp",), "Highland Vegetables"),
    ("Cabbage (Wonderball)", "PHP/kg", ("wonder",), "Highland Vegetables"),
    ("Pechay Baguio", "PHP/kg", ("pech",), "Highland Vegetables"),
    ("Red Onion (Local)", "PHP/kg", ("onion",), "Spices"),
    ("Sugar (Washed)", "PHP/kg", ("sugar",), "Other Commodities"),
    ("Fresh Pork Kasim/Pigue", "PHP/kg", ("kasim",), "Livestock And Poultry Products"),
    ("Frozen Pork Kasim/Pigue", "PHP/kg", ("kasim",), "Livestock And Poultry Products"),
    ("Fresh Pork Liempo", "PHP/kg", ("liempo",), "Livestock And Poultry Products"),
    ("Frozen Pork Liempo", "PHP/kg", ("liempo",), "Livestock And Poultry Products"),
    ("Fresh Whole Chicken", "PHP/kg", ("chicken",), "Livestock And Poultry Products"),
]

# Page-1 infographic labels mapped to (group, commodity, unit, regex)
SUMMARY_ITEMS_2024 = [
    ("Local Commercial Rice", "Special", "PHP/kg", r"\bSpecial\b"),
    ("Local Commercial Rice", "Premium", "PHP/kg", r"\bPremium\b"),
    ("Local Commercial Rice", "Well-milled Rice (Local)", "PHP/kg", r"\bWell-milled\b"),
    ("Local Commercial Rice", "Regular Milled", "PHP/kg", r"\bRegular Milled\b"),
    ("Corn", "Corn (White)", "PHP/kg", r"Corn\s*\(White\)"),
    ("Corn", "Corn (Yellow)", "PHP/kg", r"Corn\s*\(Yellow\)"),
    ("Livestock And Poultry Products", "Beef Rump", "PHP/kg", r"Beef\s*Rump"),
    ("Livestock And Poultry Products", "Beef Brisket", "PHP/kg", r"Beef\s*Brisket"),
    ("Livestock And Poultry Products", "Fresh Pork Kasim/Pigue", "PHP/kg", r"\bPork\s*Kasim\b"),
    ("Livestock And Poultry Products", "Fresh Pork Liempo", "PHP/kg", r"\bPork\s*Liempo\b"),
    ("Livestock And Poultry Products", "Frozen Pork Kasim/Pigue", "PHP/kg", r"\bFrozen\s*Kasim\b"),
    ("Livestock And Poultry Products", "Frozen Pork Liempo", "PHP/kg", r"\bFrozen\s*Liempo\b"),
    ("Livestock And Poultry Products", "Fresh Whole Chicken", "PHP/kg", r"\bWhole\s*Chicken\b"),
    ("Livestock And Poultry Products", "Egg (Medium)", "PHP/piece", r"Egg\s*\(White,?\s*Medium\)"),
    ("Fish", "Milkfish (Bangus)", "PHP/kg", r"Milkfish\s*Bangus"),
    ("Fish", "Tilapia", "PHP/kg", r"\bTilapia\b"),
    ("Fish", "Galunggong", "PHP/kg", r"Local\s*Round\s*Scad\s*Galungg\w+"),
    ("Fish", "Indian Mackerel (Alumahan)", "PHP/kg", r"Indian\s*mackerel\s*Alumahan"),
    ("Lowland Vegetables", "Ampalaya", "PHP/kg", r"Bittergourd\s*Ampalaya"),
    ("Lowland Vegetables", "String Beans (Sitao)", "PHP/kg", r"String\s*Beans\s*Sitao"),
    ("Lowland Vegetables", "Pechay Tagalog", "PHP/kg", r"Pechay\s*Tagalog"),
    ("Lowland Vegetables", "Squash (Kalabasa)", "PHP/kg", r"Squash\s*Kalabasa"),
    ("Lowland Vegetables", "Eggplant (Talong)", "PHP/kg", r"Eggplant\s*Talong"),
    ("Lowland Vegetables", "Tomato", "PHP/kg", r"Tomato\s*Kamatis"),
    ("Highland Vegetables", "Cabbage (Rareball)", "PHP/kg", r"Rare\s*Ball"),
    ("Highland Vegetables", "Cabbage (Scorpio)", "PHP/kg", r"\bScorpio\b"),
    ("Highland Vegetables", "Cabbage (Wonderball)", "PHP/kg", r"Wonder\s*Ball"),
    ("Highland Vegetables", "Carrot (Karot)", "PHP/kg", r"Carrot\s*Karot"),
    ("Highland Vegetables", "Baguio Beans", "PHP/kg", r"Baguio\s*Beans"),
    ("Highland Vegetables", "White Potato (Patatas)", "PHP/kg", r"White\s*Potato"),
    ("Highland Vegetables", "Pechay Baguio", "PHP/kg", r"Pechay\s*Baguio"),
    ("Highland Vegetables", "Chayote (Sayote)", "PHP/kg", r"Chayote\s*Sayote"),
    ("Highland Vegetables", "Cauliflower", "PHP/kg", r"\bCauliflower\b"),
    ("Fruits", "Calamansi", "PHP/kg", r"\bCalamansi\b"),
    ("Fruits", "Banana (Lakatan)", "PHP/kg", r"Banana\s*\(Lakatan\)"),
    ("Fruits", "Banana (Latundan)", "PHP/kg", r"Banana\s*\(Latundan\)"),
    ("Fruits", "Banana (Saba)", "PHP/kg", r"Banana\s*\(Saba\)"),
    ("Fruits", "Papaya", "PHP/kg", r"\bPapaya\b"),
    ("Fruits", "Mango (Carabao)", "PHP/kg", r"Mango\s*\(Carabao\)"),
    ("Fruits", "Watermelon", "PHP/kg", r"\bWatermelon\b"),
    ("Other Commodities", "Sugar (Refined)", "PHP/kg", r"Sugar\s*Refined"),
    ("Other Commodities", "Sugar (Washed)", "PHP/kg", r"Sugar\s*Washed"),
    ("Other Commodities", "Sugar (Brown)", "PHP/kg", r"Sugar\s*Brown"),
    ("Other Commodities", "Palm Oil - 350ml", "PHP/bottle", r"Palm\s*Oil\s*-\s*350ml"),
    ("Other Commodities", "Palm Oil - 1L", "PHP/bottle", r"Palm\s*Oil\s*-\s*1L"),
    ("Other Commodities", "Coconut Oil - 350ml", "PHP/bottle", r"Coconut\s*Oil\s*-\s*350ml"),
    ("Other Commodities", "Coconut Oil - 1L", "PHP/bottle", r"Coconut\s*Oil\s*-\s*1L"),
    ("Spices", "Red Onion (Local)", "PHP/kg", r"Local\s*Red\s*Onion"),
    ("Spices", "Local White Onion", "PHP/kg", r"Local\s*White\s*Onion"),
    ("Spices", "Imported Garlic", "PHP/kg", r"Imported\s*Garlic"),
    ("Spices", "Local Garlic", "PHP/kg", r"Local\s*Garlic"),
    ("Spices", "Ginger (Luya)", "PHP/kg", r"Ginger\s*Luya"),
    ("Spices", "Chili (Siling Labuyo)", "PHP/kg", r"Chil[il]\s*Siling\s*labuy\w+"),
]

GAP_TOLERANCES = (0, 0.5, 1, 1.5, 2, 3, 4, 6, 8, 10)
PRICE_FLOOR, PRICE_CEILING = 0, 10_000

DATE_RE = re.compile(
    r"(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)\w*,?\s+)?"
    r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})"
)
DAY_MONTH_DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})\b")
FILENAME_DATE_RE = re.compile(r"([A-Za-z]+)-(\d{1,2})-(\d{4})", re.IGNORECASE)
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
RANGE_OR_STATUS_RE = re.compile(
    r"(?:NOT\s+AVAIL(?:ABLE)?|TAVAIL\w*|\d[\d,]*(?:\.\d+)?\s*(?:-\s*\d[\d,]*(?:\.\d+)?)?)",
    re.IGNORECASE,
)


class ParseError(Exception):
    """Raised when the PDF does not look like the layout this parser expects."""


class SkippedImagePDFError(ParseError):
    """Raised when a PDF has no readable/selectable text and appears to be an image."""

# Small helpers

def _xc(w) -> float:
    return (w["x0"] + w["x1"]) / 2


def _yc(w) -> float:
    return (w["top"] + w["bottom"]) / 2


def is_image_only_pdf(
    pdf,
    page=None,
    min_chars: int = MIN_READABLE_CHARS,
) -> bool:
    """
    Return True if the target page (or entire PDF when page is None) has no
    meaningful readable/selectable text layer.
    """
    pages = [page] if page is not None else list(pdf.pages)
    if not pages:
        return True

    total_non_ws_chars = 0
    for p in pages:
        txt = re.sub(r"\s+", "", p.extract_text() or "")
        total_non_ws_chars += len(txt)

    return total_non_ws_chars < min_chars


def has_market_grid(pdf) -> bool:
    """Return True if the PDF appears to contain a per-market price grid."""
    for page in pdf.pages:
        up = re.sub(r"\s+", " ", (page.extract_text() or "")).upper()
        if "PER MARKET" in up or ("BALINTAWAK" in up and "QUINTA" in up):
            return True
    return False


def _empty_market_df() -> pd.DataFrame:
    df = pd.DataFrame(columns=MARKET_COLUMNS)
    df["price_min"] = pd.to_numeric(df["price_min"])
    df["price_max"] = pd.to_numeric(df["price_max"])
    df["available"] = df["available"].astype(bool)
    return df


def _empty_summary_df() -> pd.DataFrame:
    df = pd.DataFrame(columns=SUMMARY_COLUMNS)
    for col in ("low", "high", "prevailing", "average"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["market_count"] = df["market_count"].astype(int)
    return df


def _split_merged_words(words):
    """Split wide spans containing multiple price ranges or status tokens."""
    expanded = []
    for w in words:
        txt = w["text"].strip()
        matches = list(RANGE_OR_STATUS_RE.finditer(txt))
        if len(matches) <= 1 or w["x0"] < 115:
            expanded.append(w)
            continue

        total_len = max(len(txt), 1)
        span_w = max(w["x1"] - w["x0"], 1.0)
        for m in matches:
            part = m.group(0).strip()
            if not part:
                continue
            s_frac = m.start() / total_len
            e_frac = m.end() / total_len
            nw = dict(w)
            nw["text"] = part
            nw["x0"] = w["x0"] + s_frac * span_w
            nw["x1"] = w["x0"] + e_frac * span_w
            expanded.append(nw)
    return expanded


def _empty_market_fallback(report_date) -> pd.DataFrame:
    """Return placeholder rows when a PDF lacks a per-market grid."""
    rows = [
        {
            "date": report_date,
            "market": None,
            "city": None,
            "commodity": commodity,
            "unit": unit,
            "price_min": math.nan,
            "price_max": math.nan,
            "available": False,
        }
        for commodity, unit, _keys, _group in COMMODITIES
    ]
    df = pd.DataFrame(rows, columns=MARKET_COLUMNS)
    df["price_min"] = pd.to_numeric(df["price_min"])
    df["price_max"] = pd.to_numeric(df["price_max"])
    return df


def parse_cell(text: str):
    """Return (price_min, price_max, status); status is ok / na / blank / bad."""
    t = text.strip()
    if not t:
        return None, None, "blank"
    upper = t.upper()
    if "AVAIL" in upper or "TAVAIL" in upper:
        return None, None, "na"
    nums = [float(n.replace(",", "")) for n in NUMBER_RE.findall(t)]
    if not nums:
        return None, None, "bad"
    return min(nums), max(nums), "ok"


def split_market(raw: str):
    """'Maypajo Public Market/Caloocan' -> ('Maypajo Public Market', 'Caloocan')."""
    raw = re.sub(r"\s+", " ", raw).strip()
    if "/" in raw:
        market, city = raw.split("/", 1)
        return market.strip(), city.strip() or None
    return raw, None


# Locating things on the page

def find_grid_page(pdf):
    """Find the per-market grid page ('PER MARKET' or page 2 if present)."""
    for page in pdf.pages:
        txt = re.sub(r"\s+", " ", (page.extract_text() or "")).upper()
        if "PER MARKET" in txt or ("BALINTAWAK" in txt and "QUINTA" in txt):
            return page
    if len(pdf.pages) >= 2:
        return pdf.pages[1]
    raise ParseError("Could not find the per-market grid page.")


def find_summary_page(pdf):
    """Find the NCR summary page ('AT NCR MARKETS' or 'PREVAILING', falling back to page 1)."""
    for page in pdf.pages:
        txt = re.sub(r"\s+", " ", (page.extract_text() or "")).upper()
        if "AT NCR MARKETS" in txt or "PREVAILING" in txt or "NCR MARKETS" in txt:
            return page
    if pdf.pages:
        return pdf.pages[0]
    raise ParseError("Could not find the NCR summary page.")


def read_date(pdf, primary_page, pdf_path: Path | None = None):
    pages_to_check = [primary_page] + [p for p in pdf.pages if p != primary_page]
    for page in pages_to_check:
        cleaned = re.sub(r"\s+", " ", page.extract_text() or "")

        m = DATE_RE.search(cleaned)
        if m:
            month, day, year = m.groups()
            try:
                return datetime.strptime(
                    f"{month.capitalize()} {int(day)} {year}", "%B %d %Y"
                ).date()
            except ValueError:
                pass

        dm = DAY_MONTH_DATE_RE.search(cleaned)
        if dm:
            day, month, year = dm.groups()
            try:
                return datetime.strptime(
                    f"{int(day)} {month.capitalize()} {year}", "%d %B %Y"
                ).date()
            except ValueError:
                pass

    if pdf_path is not None:
        fm = FILENAME_DATE_RE.search(pdf_path.stem)
        if fm:
            month, day, year = fm.groups()
            try:
                return datetime.strptime(
                    f"{month.capitalize()} {int(day)} {year}", "%B %d %Y"
                ).date()
            except ValueError:
                pass

    raise ParseError("Could not find the report date in the PDF.")


def find_table_bounds(page, words):
    """Return (header_top, header_bottom, table_bottom) as y-coordinates."""
    banner_limit = page.height * 0.20
    banner = [
        w for w in words
        if w["top"] < banner_limit
        and (
            "PHP/KG" in w["text"].upper()
            or "COMMODITY" in w["text"].upper()
            or "PER MARKET" in re.sub(r"\s+", " ", w["text"]).upper()
        )
    ]
    header_top = max(w["bottom"] for w in banner) if banner else 0.0

    sources = [
        w["top"] for w in words
        if w["text"].startswith("Source") and w["top"] > header_top
    ]
    table_bottom = min(sources) if sources else page.height

    def _is_body_marker(txt: str) -> bool:
        up = txt.upper()
        return (
            bool(re.search(r"\d", txt))
            or "AVAIL" in up
            or "TAVAIL" in up
            or up in ("NOT", "N/A")
        )

    body_tops = [
        w["top"] for w in words
        if header_top <= w["top"] < table_bottom and _is_body_marker(w["text"])
    ]

    if not body_tops and header_top > 0:
        header_top = 0.0
        body_tops = [
            w["top"] for w in words
            if 0.0 <= w["top"] < table_bottom and _is_body_marker(w["text"])
        ]

    if not body_tops:
        return header_top, header_top, table_bottom

    return header_top, min(body_tops) - 0.5, table_bottom


def _anchor_bands(words, expected: int):
    """Fallback vertical bands anchored around known column x-centers if gap clustering differs."""
    default_centers = [
        75.0, 136.0, 171.0, 206.0, 242.0, 277.0, 310.0, 345.0, 381.0, 415.0,
        449.0, 483.0, 520.0, 558.0, 598.0, 636.0, 675.0, 715.0, 755.0, 795.0,
    ][:expected]
    bands = []
    for idx, cx in enumerate(default_centers):
        half_w = 24.0 if idx == 0 else 15.0
        bands.append([cx - half_w, cx + half_w])
    return bands


def cluster_columns(words, expected, strict: bool = True):
    spans = sorted((w["x0"], w["x1"]) for w in words)
    seen = {}
    for tol in GAP_TOLERANCES:
        clusters = []
        for x0, x1 in spans:
            if clusters and x0 <= clusters[-1][1] + tol:
                clusters[-1][1] = max(clusters[-1][1], x1)
            else:
                clusters.append([x0, x1])
        seen[tol] = len(clusters)
        if len(clusters) == expected:
            return clusters

    if not strict:
        return _anchor_bands(words, expected)

    raise ParseError(
        f"Expected {expected} columns (market name + {expected - 1} commodities) "
        f"but found these counts for each gap tolerance tried: {seen}. "
        "Run `python parser.py file.pdf` to inspect."
    )


def group_lines(words):
    if not words:
        return []
    tol = 0.5 * statistics.median(w["bottom"] - w["top"] for w in words)
    lines = []
    for w in sorted(words, key=_yc):
        if lines and abs(_yc(w) - lines[-1]["y"]) <= tol:
            lines[-1]["words"].append(w)
            lines[-1]["y"] = statistics.mean(_yc(x) for x in lines[-1]["words"])
        else:
            lines.append({"y": _yc(w), "words": [w]})
    return lines


# 1. Per-market grid entry point

def parse_market_page(
    pdf_path,
    strict: bool = True,
    skip_images: bool = True,
    raise_on_skip: bool = False,
) -> pd.DataFrame:
    pdf_path = Path(pdf_path)
    n_cols = 1 + len(COMMODITIES)

    with pdfplumber.open(pdf_path) as pdf:
        if skip_images and is_image_only_pdf(pdf):
            msg = f"Skipping '{pdf_path.name}': no readable text (appears to be an image)."
            if raise_on_skip:
                raise SkippedImagePDFError(msg)
            return _empty_market_df()

        if not has_market_grid(pdf):
            if not strict:
                first_page = pdf.pages[0] if pdf.pages else None
                report_date = read_date(pdf, first_page, pdf_path=pdf_path) if first_page else None
                return _empty_market_fallback(report_date)
            raise ParseError(
                f"'{pdf_path.name}' has no per-market grid page (likely a summary-only PDF)."
            )

        page = find_grid_page(pdf)
        if skip_images and is_image_only_pdf(pdf, page=page):
            msg = (
                f"Skipping '{pdf_path.name}' grid page: no readable text "
                "(appears to be an image)."
            )
            if raise_on_skip:
                raise SkippedImagePDFError(msg)
            return _empty_market_df()

        report_date = read_date(pdf, page, pdf_path=pdf_path)
        words = _split_merged_words(page.extract_words())

    header_top, header_bottom, table_bottom = find_table_bounds(page, words)
    region = [
        w for w in words
        if w["top"] >= header_top - 0.5 and w["bottom"] <= table_bottom + 0.5
    ]

    if not region:
        if strict:
            raise ParseError("Could not locate any price cells below the header.")
        return _empty_market_fallback(report_date)

    bands = cluster_columns(region, n_cols, strict=strict)
    edges = [(bands[i][1] + bands[i + 1][0]) / 2 for i in range(len(bands) - 1)]

    def col_of(w):
        return bisect.bisect(edges, _xc(w))

    header_words = [w for w in region if w["bottom"] <= header_bottom + 0.5]
    for i, (name, _unit, keys, _group) in enumerate(COMMODITIES, start=1):
        parts = sorted(
            (w for w in header_words if col_of(w) == i),
            key=lambda w: (round(w["top"]), w["x0"]),
        )
        text = "".join(w["text"] for w in parts).lower()

        if keys and not any(k in text for k in keys):
            msg = (
                f"Column {i} should be '{name}' but its header reads {text!r} "
                f"(expected one of {keys})."
            )
            if strict:
                raise ParseError(msg)

    body = [w for w in region if w["top"] >= header_bottom - 0.5]
    name_lines = group_lines([w for w in body if col_of(w) == 0])
    if not body or len(name_lines) < 2:
        if strict:
            raise ParseError("Could not find market rows or price cells on grid page.")
        return _empty_market_fallback(report_date)

    centers = [ln["y"] for ln in name_lines]
    pitch = statistics.median(b - a for a, b in zip(centers, centers[1:]))
    max_dist = 0.6 * pitch

    cells = {}
    for w in body:
        r = min(range(len(centers)), key=lambda i: abs(centers[i] - _yc(w)))
        if abs(centers[r] - _yc(w)) > max_dist:
            continue
        cells.setdefault((r, col_of(w)), []).append(w)

    def cell_text(r, c):
        return " ".join(
            w["text"] for w in sorted(cells.get((r, c), []), key=lambda w: w["x0"])
        )

    records, problems = [], []
    for r in range(len(centers)):
        market, city = split_market(cell_text(r, 0))
        if not market:
            continue
        for c, (commodity, unit, _keys, _group) in enumerate(COMMODITIES, start=1):
            lo, hi, status = parse_cell(cell_text(r, c))
            if status in ("blank", "bad"):
                problems.append(
                    f"{market} / {commodity}: {status} cell {cell_text(r, c)!r}"
                )
            records.append({
                "date": report_date,
                "market": market,
                "city": city,
                "commodity": commodity,
                "unit": unit,
                "price_min": lo,
                "price_max": hi,
                "available": status == "ok",
            })

    df = pd.DataFrame(records, columns=MARKET_COLUMNS)
    df["price_min"] = pd.to_numeric(df["price_min"])
    df["price_max"] = pd.to_numeric(df["price_max"])

    if strict:
        problems += validate(df)
        if problems:
            shown = "\n  - ".join(problems[:10])
            more = f"\n  ... and {len(problems) - 10} more" if len(problems) > 10 else ""
            raise ParseError(f"{pdf_path.name} failed validation:\n  - {shown}{more}")
    return df


# 2. Daily NCR summary entry point (keeps NaN + provenance columns)

def _compute_market_stats(
    market_df: pd.DataFrame | None,
) -> dict[str, tuple[float, float, float, float, int]]:
    """Compute (low, high, prevailing, average, market_count) per commodity from market_df."""
    if market_df is None or market_df.empty:
        return {}
    avail = market_df[market_df["available"]].copy()
    if avail.empty:
        return {}
    avail["mid"] = (avail["price_min"] + avail["price_max"]) / 2.0
    stats = {}
    for commodity, grp in avail.groupby("commodity"):
        cnt = int(len(grp))
        lo = float(grp["price_min"].min())
        hi = float(grp["price_max"].max())
        modes = grp["mid"].mode()
        prev = float(modes.iloc[0]) if not modes.empty else float(grp["mid"].median())
        avg = round(float(grp["mid"].mean()), 2)
        stats[commodity] = (lo, hi, prev, avg, cnt)
    return stats


def parse_daily_summary(
    pdf_path,
    market_df: pd.DataFrame | None = None,
    skip_images: bool = True,
    raise_on_skip: bool = False,
) -> pd.DataFrame:
    """
    Extract the daily NCR summary table with NaN preserved when unavailable
    and explicit provenance columns:
        date, group, commodity, unit, low, high, prevailing, average,
        market_count, prevailing_source, average_source
    """
    pdf_path = Path(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        if skip_images and is_image_only_pdf(pdf):
            msg = f"Skipping '{pdf_path.name}': no readable text (appears to be an image)."
            if raise_on_skip:
                raise SkippedImagePDFError(msg)
            return _empty_summary_df()

        page = find_summary_page(pdf)
        if skip_images and is_image_only_pdf(pdf, page=page):
            msg = (
                f"Skipping '{pdf_path.name}' summary page: no readable text "
                "(appears to be an image)."
            )
            if raise_on_skip:
                raise SkippedImagePDFError(msg)
            return _empty_summary_df()

        report_date = read_date(pdf, page, pdf_path=pdf_path)
        raw_text = page.extract_text() or ""

    market_stats = _compute_market_stats(market_df)
    normalized = re.sub(r"\s+", " ", raw_text)

    records = []
    for group, commodity, unit, pattern in SUMMARY_ITEMS_2024:
        m = re.search(pattern, normalized, flags=re.IGNORECASE)
        lo = hi = prev = avg = math.nan
        market_count = 0

        if m:
            window = normalized[m.end(): m.end() + 90]
            if not re.match(r"^\s*NOT\s+AVAIL", window, flags=re.IGNORECASE):
                rm = re.search(
                    r"(\d[\d,]*\.\d{2})\s*(?:[*]+\s*)?(?:-\s*(\d[\d,]*\.\d{2}))?",
                    window,
                )
                if rm:
                    v1 = float(rm.group(1).replace(",", ""))
                    v2 = float(rm.group(2).replace(",", "")) if rm.group(2) else v1
                    lo, hi = min(v1, v2), max(v1, v2)

        if commodity in market_stats:
            m_lo, m_hi, m_prev, m_avg, m_cnt = market_stats[commodity]
            market_count = m_cnt
            if math.isnan(lo) or math.isnan(hi):
                lo, hi = m_lo, m_hi
            prev, avg = m_prev, m_avg

        has_stats = not math.isnan(prev) and not math.isnan(avg)
        records.append({
            "date": report_date,
            "group": group,
            "commodity": commodity,
            "unit": unit,
            "low": lo,
            "high": hi,
            "prevailing": prev,
            "average": avg,
            "market_count": market_count,
            "prevailing_source": "market_derived" if has_stats else "missing_no_market_data",
            "average_source": "market_derived" if has_stats else "missing_no_market_data",
        })

    df = pd.DataFrame(records, columns=SUMMARY_COLUMNS)
    for col in ("low", "high", "prevailing", "average"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["market_count"] = pd.to_numeric(df["market_count"], errors="coerce").fillna(0).astype(int)
    return df


def validate(df: pd.DataFrame):
    problems = []
    if df.empty:
        return problems
    n = df["market"].nunique()
    if EXPECTED_MARKETS is not None and n != EXPECTED_MARKETS:
        problems.append(f"expected {EXPECTED_MARKETS} markets, found {n}")
    avail = df[df["available"]]
    bad = avail[avail["price_min"] > avail["price_max"]]
    if len(bad):
        problems.append(f"{len(bad)} rows have price_min > price_max")
    out = avail[
        (avail["price_min"] <= PRICE_FLOOR) | (avail["price_max"] > PRICE_CEILING)
    ]
    if len(out):
        problems.append(f"{len(out)} prices outside {PRICE_FLOOR}-{PRICE_CEILING}")
    if df.groupby("market")["available"].sum().eq(0).any():
        problems.append("at least one market has no available prices at all")
    return problems