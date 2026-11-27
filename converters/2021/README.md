# 2021 DA Price Monitoring Converters

Scripts used to parse 2021 Department of Agriculture (DA) NCR daily price monitoring PDFs into per-date and combined CSVs.

## Scripts
- `classify_pdfs.py` — Classifies raw PDFs by layout type (standard, infographic, or image-only).
- `parse_2021.py` — Parses standard 2021 PDFs into summary and market price CSVs.
- `parse_2021_infographic.py` — Parses infographic-style 2021 PDFs.
- `split_per_date.py` — Splits parsed outputs into per-date CSVs (`ncr_summary_per_date/` and `markets_per_date/`).
- `make_all.py` — Builds `all_ncr_summary.csv` and `all_markets.csv` in the standard combined layout.
- `manual_*.csv` — Hand-typed summary and market data for image-only PDFs.

## Usage
1. Place raw 2021 PDFs in your working data folder.
2. Run `classify_pdfs.py`, then `parse_2021.py` and `parse_2021_infographic.py`.
3. Run `split_per_date.py` to generate per-date files.
4. Run `make_all.py` to generate `all_ncr_summary.csv` and `all_markets.csv`.
