import argparse 
import csv
import os
import re
import sys
import time

from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pdfplumber

NA = "NOT AVAILABLE"

MARKET_DIR = "Per Market"
SUMMARY_DIR = "NCR Summary"

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def plausible_market(name):
    return (3 <= len(name) <= 60
            and not re.search(r"\d", name)
            and "NOT AVAIL" not in name.upper())

def plausible_commodity(name):
    return (2 <= len(name) <= 50
            and not re.search(r"\d+\.\d{2}", name)
            and "NOT AVAIL" not in name.upper())