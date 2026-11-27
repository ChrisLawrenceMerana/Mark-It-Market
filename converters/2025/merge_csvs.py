#!/usr/bin/env python3
"""
Merge the CSVs made by pdf_to_csv.py into two files:

    merged_markets.csv      (daily per-market prices, tidy format)
    merged_ncr_summary.csv  (NCR-wide Low / High / Prevailing / Average)

It accepts any mix of what pdf_to_csv.py produces and tells them apart from
their headers:
    - all_markets.csv / all_ncr_summary.csv   (from --combine)
    - <name>.csv / <name>_ncr_summary.csv     (one pair per PDF)
Per-market files in the wide format (one row per market) are converted to the
tidy format automatically.

    python merge_csvs.py                  # merges the CSVs in the current folder and in
                                          # its "Per Market" and "NCR Summary" folders
    python merge_csvs.py a/*.csv b/*.csv  # or give specific files / wildcards

Rows are sorted by date and exact duplicate rows are removed.
"""
import argparse
import csv
import glob
import re
import sys
from pathlib import Path

MARKETS_HEADER = ["date", "market", "commodity", "unit", "price_low", "price_high",
                  "source_file"]
SUMMARY_HEADER = ["date", "group", "commodity", "unit", "low", "high", "prevailing",
                  "average", "source_file"]

OUTPUTS = {"markets": "merged_markets.csv", "summary": "merged_ncr_summary.csv"}
IGNORE = set(OUTPUTS.values()) | {"conversion_errors.csv", "coverage.csv", "coverage_log.csv"}
WIDE_COL = re.compile(r"^(?P<name>.+) (?P<end>Low|High) \((?P<unit>[^)]+)\)$")


def read_file(path):
    """Returns (kind, rows) with rows in the standard tidy layout.
    Raises ValueError for bad files."""
    src = Path(path).name
    with open(path, newline="", encoding="utf-8-sig") as f:       # strips the BOM
        r = csv.reader(f)
        header = next(r, None) or []
        body = [row for row in r if row]

    if header and header[:2] == ["date", "market"] and "price_low" not in header:
        # wide per-PDF file: date, market, <name> Low (<unit>), <name> High (<unit>), ...
        cols = []
        for i, h in enumerate(header[2:], 2):
            m = WIDE_COL.match(h)
            if not m:
                raise ValueError("unrecognised column in a markets file: " + h)
            cols.append((i, m))
        pairs = {}
        for i, m in cols:
            key = (m["name"], m["unit"])
            pairs.setdefault(key, {})[m["end"]] = i
        rows = []
        for row in body:
            for (name, unit), idx in pairs.items():
                rows.append([row[0], row[1], name, unit,
                             row[idx["Low"]], row[idx["High"]], src])
        return "markets", rows

    if "market" in header and "price_low" in header:
        std = MARKETS_HEADER[:-1]
        kind, want = "markets", std
    elif "prevailing" in header and "average" in header:
        std = SUMMARY_HEADER[:-1]
        kind, want = "summary", std
    else:
        raise ValueError("not a markets or summary file (columns: "
                         + ", ".join(header[:4]) + ("..." if len(header) > 4 else "") + ")")

    if header[:len(want)] != want:
        raise ValueError("unexpected columns; made by a different version of pdf_to_csv.py?")
    rows = []
    for row in body:
        row = row + [""] * (len(want) - len(row))
        source = row[len(want)] if len(row) > len(want) and row[len(want)] else src
        out = [row[0]] + row[1:len(want)] + [source]
        rows.append(out)
    return kind, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*",
                    help="CSV files (default: .csv files in the current folder and in "
                         "its 'Per Market' and 'NCR Summary' folders)")
    ap.add_argument("-d", "--outdir", default=".", help="where to write the merged files")
    args = ap.parse_args()

    paths = []
    default = ["*.csv", "Per Market/*.csv", "NCR Summary/*.csv"]
    for pat in args.files or default:
        paths.extend(sorted(glob.glob(pat)) or [pat])
    paths = [p for p in dict.fromkeys(paths) if Path(p).name not in IGNORE]
    if not paths:
        sys.exit("No CSV files found in this folder or its 'Per Market' / "
                 "'NCR Summary' folders.")

    data = {k: {"rows": [], "seen": set(), "dupes": 0, "files": 0} for k in OUTPUTS}
    skipped = []
    for p in paths:
        try:
            kind, rows = read_file(p)
        except (OSError, ValueError, IndexError) as e:
            skipped.append((p, str(e)))
            print(f"SKIPPED {p}: {e}", file=sys.stderr)
            continue
        d = data[kind]
        n = 0
        for row in rows:
            t = tuple(row)
            if t in d["seen"]:
                d["dupes"] += 1
                continue
            d["seen"].add(t)
            d["rows"].append(row)
            n += 1
        d["files"] += 1
        print(f"{p}: {n} {kind} rows", file=sys.stderr)


    headers = {"markets": MARKETS_HEADER, "summary": SUMMARY_HEADER}
    wrote = 0
    for kind, d in data.items():
        if not d["rows"]:
            print(f"No {kind} data found, so {OUTPUTS[kind]} was not created.",
                  file=sys.stderr)
            continue
        d["rows"].sort(key=lambda x: (x[0], x[-1]))
        out = Path(args.outdir) / OUTPUTS[kind]
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(headers[kind])
            w.writerows(d["rows"])
        print(f"Wrote {out}: {len(d['rows'])} rows from {d['files']} file(s) "
              f"({d['dupes']} exact duplicates removed)", file=sys.stderr)
        wrote += 1
    if not wrote:
        sys.exit("Nothing was merged.")


if __name__ == "__main__":
    main()
