# Bantay Presyo PDF to CSV (`Mark-It-Market_2024CSV`) 

Extracts structured pricing data from Department of Agriculture (DA) Bantay Presyo PDF price-monitoring reports into CSV datasets: 

1. **Per-market price grid (`parse_market_page`)** — Extracts commodity price ranges (`price_min`, `price_max`) and availability (`available`) across markets and cities (`market`, `city`, `commodity`, `unit`, `date`). 

2. **Daily NCR summary (`parse_daily_summary`)** — Extracts summary commodity figures (`low`, `high`, `prevailing`, `average`) along with market counts and data-provenance columns (`prevailing_source`, `average_source`). 

## Project Structure ``` 
├── .gitignore 
├── README.md 
├── requirements.txt 
├── parser.py 
├── pdf_to_csv.py 
├── convert.bat 
├── multipdf_to_csv.py 
├── multi_convert.bat 
├── Input/
│   └── .gitkeep 
└── Output/            # Created automatically when converting PDFs 

``` > **Note:** `.gitignore` ignores `.venv/`, `__pycache__/`, `*.csv`, and `*.pdf` so virtual environments, cached bytecode, raw PDFs, and generated CSVs are kept out of version control. 

## Setup 

### 1. Create and Activate a Virtual Environment (Windows) 

bat 
python -m venv .venv 
.venv\Scripts\activate 

### 2. Install Dependencies 

bash pip install 
-r requirements.txt 

``` Pinned packages in `requirements.txt`:

 * `pdfplumber` and `pdfminer.six` (PDF text and layout/word-box extraction) 
 * `pypdfium2` and `pillow` (PDF rendering/image support) 
 * `pandas` and `numpy` (DataFrame construction, validation, and CSV export) 
 * `python-dateutil`, `tzdata`, `cffi`, `charset-normalizer`, `cryptography`, `pycparser` 
 
 ## Usage 
 
 ### Inspect a Single PDF in the Terminal 
 
 Run `parser.py` directly to inspect detected report date, market/commodity counts, available cell counts, validation results, and sample rows: 
 
 bash 
 python parser.py "Input/Price-Monitoring-Report.pdf" 
 
 ### Convert One or More PDFs to Individual CSVs Use 
 
 `pdf_to_csv.py` to convert specific PDFs into per-file CSVs: 
 
 bash python pdf_to_csv.py path/to/file1.pdf path/to/file2.pdf -o Output  
 
 * If `-o` / `--outdir` is omitted, each CSV is written next to its source PDF. 
 * On Windows, you can also run `convert.bat` (which uses `.venv\Scripts\python.exe` and writes to `Output`). 
 
 ### Batch-Convert a Folder of PDFs 
 
 Place PDF reports in the `Input/` folder (or pass a custom folder with `-i`), then run `multipdf_to_csv.py`: 
 
 bash python multipdf_to_csv.py 
 -i Input -o Output  
 
 * `-i` / `--indir` expects an input folder path (default: `Input`). 
 * `-o` / `--outdir` expects an **output folder path** (default: `Output`), not a `.csv` filename. 
 * On Windows, you can double-click or run `multi_convert.bat`. 
 
 ## Output Schemas 
 
 ### Per-Market Grid CSV 
 | Column | Description | 
 | :--- | :--- | 
 | `date` | Report date parsed from the PDF or filename | 
 | `market` | Market name (before `/` if split) | 
 | `city` | City or locality (after `/` if present) | 
 | `commodity` | Standardized commodity name | 
 | `unit` | Unit of measurement (e.g., `PHP/kg`, `PHP/piece`) | 
 | `price_min` | Minimum price in the cell | 
 | `price_max` | Maximum price in the cell (equals `price_min` for single values) | 
 | `available` | `True` when numeric price data was parsed, `False` otherwise | 
 
 ### Daily Summary CSV 
 
 | Column | Description | 
 | :--- | :--- | 
 | `date` | Report date | 
 | `group` | Commodity category group | 
 | `commodity` | Commodity name from the summary mapping | 
 | `unit` | Unit of measurement (e.g., `PHP/kg`, `PHP/bottle`, `PHP/piece`) | 
 | `low` | Lowest price observed | | `high` | Highest price observed | 
 | `prevailing` | Prevailing/modal/median price when derived | 
 | `average` | Average price when derived | 
 | `market_count` | Number of markets with available data for the commodity | 
 | `prevailing_source` | Provenance indicator (e.g., `market_derived` or `missing_no_market_data`) | 
 | `average_source` | Provenance indicator (e.g., `market_derived` or `missing_no_market_data`) | 
 
 ## Troubleshooting 
 
 * **Image-only / scanned PDFs:** PDFs with no selectable text layer may be skipped (or raise `SkippedImagePDFError` when `raise_on_skip=True`). 
 * **Layout or header mismatches:** If column clustering or header checks fail on an unusual PDF layout, run `python parser.py <file.pdf>` with `strict=False` in inspection mode or check `ParseError` messages for details. 
 * **Git / VS Code path errors:** Make sure you open the actual local cloned repository folder on disk (with a valid drive/user path) rather than a missing or mistyped folder path.