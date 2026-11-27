import argparse
from pathlib import Path

from parser import parse_market_page, ParseError


def main():
    ap = argparse.ArgumentParser(description="Convert Bantay Presyo PDFs to CSV")
    ap.add_argument("files", nargs="+", type=Path, help="PDF file(s) to convert")
    ap.add_argument("-o", "--outdir", type=Path, help="Folder for the CSVs (default: next to each PDF)")
    args = ap.parse_args()

    for pdf in args.files:
        try:
            df = parse_market_page(pdf)
            outdir = args.outdir or pdf.parent
            outdir.mkdir(parents=True, exist_ok=True)
            out = outdir / f"{pdf.stem}.csv"
            df.to_csv(out, index=False)
            print(f"OK      {pdf.name} -> {out.name} ({len(df)} rows)")
        except ParseError as e:
            print(f"FAILED  {pdf.name}: {e}")
        except Exception as e:
            print(f"ERROR   {pdf.name}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()