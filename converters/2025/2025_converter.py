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