#!/usr/bin/env python3
"""
Download the "Daily Retail Price Range" PDFs from https://www.da.gov.ph/price-monitoring/

Companion to pdf_to_csv_2023.py. It reads the price-monitoring page, finds every daily
price-range PDF link for the chosen year (default 2023), and downloads them one by one
as  Price-Monitoring-<Month>-<day>-<year>.pdf  (the name the converter expects, even when
the site's own file is called "Revised-..." or "...-Revised"). 

Folders (created next to wherever you run it):
    <out>/pdfs/     the downloaded PDFs
    <out>/          with --convert: 'Per Market' and 'NCR Summary' CSVs
    <out>/download_log.csv   one line per file: date, url, saved name, status

Typical use
    python download_pdfs.py --test --convert     # 6 PDFs spread over the year, then convert
    python download_pdfs.py --list               # just show what would be downloaded
    python download_pdfs.py --convert            # the whole year, then convert

Re-running is safe: files already downloaded are skipped, so an interrupted run resumes.
If the site refuses the script (HTTP 403) or the list is empty, open the page in your
browser, save it (Ctrl+S, "Webpage, HTML only") and run:
    python download_pdfs.py --html "saved page.html" --test
"""

import argparse
import csv
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

PAGE_URL = "https://www.da.gov.ph/price-monitoring/"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# Links on the page that are NOT the daily retail price range
EXCLUDE = re.compile(r"weekly|cigarette|index", re.IGNORECASE)
LABEL_DATE = re.compile(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})")
NAME_DATE = re.compile(r"([A-Za-z]+)-(\d{1,2})-(\d{4})")
REVISED = re.compile(r"revis|updated|final", re.IGNORECASE)


class LinkParser(HTMLParser):
    """Collects (href, visible text) for every <a> tag."""

    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def parse_date(month, day, year):
    for f in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(f"{month} {day} {year}", f).date()
        except ValueError:
            pass
    return None


def find_daily_links(html, base_url, year):
    """-> sorted [(date, absolute_url)] of daily price-range PDFs for `year`."""
    parser = LinkParser()
    parser.feed(html)
    found, notes = {}, []
    for href, text in parser.links:
        path = urllib.parse.urlsplit(href).path
        name = path.rsplit("/", 1)[-1]
        if not name.lower().endswith(".pdf") or "price-monitoring" not in name.lower():
            continue
        if EXCLUDE.search(name):
            continue
        m = LABEL_DATE.search(text)
        d = parse_date(*m.groups()) if m else None          # the label on the page first
        n = NAME_DATE.search(name)
        from_name = parse_date(*n.groups()) if n else None  # then the file name
        if d is None:
            d = from_name
        if d is None or d.year != year:
            continue
        if from_name and from_name != d:
            notes.append(f"label '{text}' but file is '{name}'; using the label's date")
        url = urllib.parse.urljoin(base_url, href)
        url = urllib.parse.quote(url, safe=":/%?&=#")
        if d in found:                                       # two links for one day
            old = found[d]
            if REVISED.search(old.rsplit("/", 1)[-1]) or not REVISED.search(name):
                notes.append(f"{d}: two links, kept {old.rsplit('/', 1)[-1]}")
                continue
            notes.append(f"{d}: two links, kept {name}")
        found[d] = url
    return sorted(found.items()), notes


def read_page(args):
    if args.html:
        return Path(args.html).read_text(encoding="utf-8", errors="replace"), PAGE_URL
    req = urllib.request.Request(PAGE_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", errors="replace"), PAGE_URL
    except urllib.error.HTTPError as e:
        sys.exit(f"The site answered HTTP {e.code}. Save the page from your browser and "
                 "use  --html \"saved page.html\"  (see --help).")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach {PAGE_URL}: {e.reason}")


def pick_test_files(items, count):
    """`count` files spread evenly from the first to the last day."""
    if len(items) <= count:
        return items
    idx = sorted({round(i * (len(items) - 1) / (count - 1)) for i in range(count)})
    return [items[i] for i in idx]


def download(url, dest, retries=3):
    """Download to dest (via a .part file). Returns '' on success or an error text."""
    last = ""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            if not data.startswith(b"%PDF"):
                return "not a PDF (the site returned something else)"
            part = dest.with_suffix(".part")
            part.write_bytes(data)
            part.replace(dest)
            return ""
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (403, 404):
                return last                                  # retrying will not help
        except Exception as e:                               # timeouts, resets...
            last = str(e)
        time.sleep(2 * attempt)
    return last


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=2023, help="year to download (default 2023)")
    ap.add_argument("--test", action="store_true",
                    help="download only a small test set spread over the year (default 6)")
    ap.add_argument("--limit", type=int, help="download only the first N files (oldest first)")
    ap.add_argument("--list", action="store_true", help="list the files found; download nothing")
    ap.add_argument("--convert", action="store_true",
                    help="after downloading, run pdf_to_csv_2023.py --all --combine on them")
    ap.add_argument("--out", help="output folder (default: download_<year>, or test_<year>)")
    ap.add_argument("--html", help="use a saved copy of the web page instead of fetching it")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds to wait between downloads (default 1.0, be polite to the site)")
    args = ap.parse_args()

    html, base = read_page(args)
    items, notes = find_daily_links(html, base, args.year)
    if not items:
        sys.exit(f"No daily price-range PDF links for {args.year} were found on the page. "
                 "If the page loads its tables with JavaScript, save it from the browser "
                 "after expanding the year and use --html.")
    print(f"Found {len(items)} daily PDFs for {args.year} "
          f"({items[0][0]:%d %b %Y} to {items[-1][0]:%d %b %Y}).")
    for n in notes:
        print("  !", n)

    if args.test:
        items = pick_test_files(items, 6)
    if args.limit:
        items = items[:args.limit]
    if args.list:
        for d, url in items:
            print(f"  {d}  {url}")
        return

    out = Path(args.out or (f"test_{args.year}" if args.test else f"download_{args.year}"))
    pdf_dir = out / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {len(items)} file(s) into {pdf_dir}")
    log, failed = [], 0
    for i, (d, url) in enumerate(items, 1):
        name = f"Price-Monitoring-{d:%B}-{d.day}-{d.year}.pdf"
        dest = pdf_dir / name
        if dest.exists() and dest.stat().st_size > 0:
            status = "already downloaded"
        else:
            err = download(url, dest)
            status = "ok" if not err else f"FAILED: {err}"
            if err:
                failed += 1
            if i < len(items):
                time.sleep(args.delay)
        print(f"[{i}/{len(items)}] {name}  {status}")
        log.append([d.isoformat(), url, name, status])
    with open(out / "download_log.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["date", "url", "saved_as", "status"])
        w.writerows(log)
    print(f"\nDone: {len(items) - failed} ok, {failed} failed. Log: {out / 'download_log.csv'}")
    if failed:
        print("Run the same command again to retry only the failed ones.")

    if args.convert:
        script = Path(__file__).with_name("pdf_to_csv_2023.py")
        print("\nConverting...")
        subprocess.run([sys.executable, str(script.resolve()), "--all", "--combine",
                        "--outdir", str(out.resolve())],
                       cwd=pdf_dir, stdin=subprocess.DEVNULL)
        print(f"CSVs are in {out / 'Per Market'} and {out / 'NCR Summary'}")


if __name__ == "__main__":
    main()