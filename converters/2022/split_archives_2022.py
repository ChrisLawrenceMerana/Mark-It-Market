import os
import pandas as pd

os.makedirs("Per Market", exist_ok=True)
os.makedirs("NCR Summary", exist_ok=True)

mkt = pd.read_csv("all_markets_2022.csv")
ncr = pd.read_csv("all_ncr_summary_2022.csv")

for sf, g in mkt.groupby("source_file"):
    g.to_csv(f"Per Market/{sf[:-4]}.csv", index=False, encoding="utf-8-sig")

for sf, g in ncr.groupby("source_file"):
    g.to_csv(f"NCR Summary/{sf[:-4]}_ncr_summary.csv", index=False, encoding="utf-8-sig")
