import argparse
from pathlib import Path
import pandas as pd

from parser import ParseError, parse_daily_summary, parse_market_page


def main():
    ap = argparse.ArgumentParser(
        description="Convert all PDFs in an input folder into combined market prices and daily summary CSVs"
    )
    ap.add_argument(
        "input_dir",
        type=Path,
        help="Folder containing input .pdf files",
    )
    ap.add_argument(
        "-o",
        "--outdir",
        type=Path,
        default=Path("Output"),
        help="Output folder for the combined CSVs (default: Output)",
    )
    ap.add_argument(
        "--recursive",
        action="store_true",
        help="Also search subfolders for .pdf files",
    )
    ap.add_argument(
        "--no-strict",
        action="store_true",
        help="Run market parser with strict=False for messy/clipped PDFs",
    )
    args = ap.parse_args()

    if not args.input_dir.is_dir():
        raise SystemExit(f"Input folder does not exist: {args.input_dir}")

    pattern = "**/*.pdf" if args.recursive else "*.pdf"
    pdf_files = sorted(p for p in args.input_dir.glob(pattern) if p.is_file())

    if not pdf_files:
        raise SystemExit(f"No .pdf files found in: {args.input_dir}")

    market_frames = []
    summary_frames = []

    for pdf in pdf_files:
        market_df = None
        try:
            market_df = parse_market_page(pdf, strict=not args.no_strict)
            market_df["source_file"] = pdf.name
            market_frames.append(market_df)
            print(f"OK [market]   {pdf.name} ({len(market_df)} rows)")
        except ParseError as e:
            print(f"FAILED [market] {pdf.name}: {e}")
        except Exception as e:
            print(f"ERROR [market]  {pdf.name}: {type(e).__name__}: {e}")

        try:
            summary_df = parse_daily_summary(pdf, market_df=market_df)
            summary_df["source_file"] = pdf.name
            summary_frames.append(summary_df)
            print(f"OK [summary]  {pdf.name} ({len(summary_df)} rows)")
        except ParseError as e:
            print(f"FAILED [summary] {pdf.name}: {e}")
        except Exception as e:
            print(f"ERROR [summary]  {pdf.name}: {type(e).__name__}: {e}")

    args.outdir.mkdir(parents=True, exist_ok=True)

    if market_frames:
        market_out = args.outdir / "combined_market_prices.csv"
        combined_market = pd.concat(market_frames, ignore_index=True)
        combined_market.to_csv(market_out, index=False)
        print(f"\nSaved {len(combined_market)} market rows to {market_out}")
    else:
        print("\nNo per-market data parsed.")

    if summary_frames:
        summary_out = args.outdir / "combined_daily_summary.csv"
        combined_summary = pd.concat(summary_frames, ignore_index=True)
        combined_summary.to_csv(summary_out, index=False)
        print(f"Saved {len(combined_summary)} summary rows to {summary_out}")
    else:
        print("No daily summary data parsed.")


if __name__ == "__main__":
    main()