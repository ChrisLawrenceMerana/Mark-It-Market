import re
import numpy as np
import pandas as pd

# 1. Clean all_markets_2022.csv
mkt = pd.read_csv("all_markets_2022.csv")
mkt["commodity"] = mkt["commodity"].replace({"Whole": "Fresh Whole Chicken"})

# Fix 3700.0 decimal typo on 2022-12-30 Dagonoy Market Frozen Pork Liempo
typo = (mkt["commodity"] == "Frozen Pork Liempo") & (mkt["price_low"] == 3700.0)
mkt.loc[typo, ["price_low", "price_high"]] = 370.0

# Fix early-Jan Egg rows (>20) and June/July veg/fish rows (<20) caused by column shift
egg_high = (mkt["commodity"] == "Egg (Medium)") & (mkt["price_high"] > 20)
mkt.loc[egg_high, "price_high"] = mkt.loc[egg_high, "price_low"]

veg_fish = ["Ampalaya", "Cabbage", "Galunggong", "Tilapia", "Tomato", "Pechay Baguio", "Red Onion (Local)", "Sugar (Washed)", "Well-milled Rice (Local)"]
shifted_egg = mkt["commodity"].isin(veg_fish) & (mkt["price_low"] < 20)
mkt.loc[shifted_egg, ["price_low", "price_high"]] = np.nan

mkt = mkt.drop_duplicates(subset=["date", "market", "commodity", "source_file"]).sort_values(["date", "market", "commodity"])
mkt.to_csv("all_markets_2022.csv", index=False, encoding="utf-8-sig")

# 2. Clean all_ncr_summary_2022.csv using canonical DA commodity matching
CANONICAL_NCR = [
    ("Special", "Special Rice"),
    ("Premium", "Premium Rice"),
    ("Well-milled", "Well-milled Rice"),
    ("Regular Milled", "Regular Milled Rice"),
    ("Beef Rump", "Beef Rump"),
    ("Beef Brisket", "Beef Brisket"),
    ("Pork Kasim", "Pork Kasim/Pigue"),
    ("Pork Liempo", "Pork Liempo"),
    ("Whole Chicken", "Whole Chicken"),
    ("Egg (medium)", "Egg (Medium)"),
    ("Milkfish|Bangus", "Milkfish (Bangus)"),
    ("Tilapia", "Tilapia"),
    ("Local Round Scad", "Local Round Scad (Galunggong)"),
    ("Imported Round Scad", "Imported Round Scad (Galunggong)"),
    ("Galunggong", "Galunggong"),
    ("Alumahan", "Indian Mackerel (Alumahan)"),
    ("Ampalaya|Bittergourd", "Bittergourd (Ampalaya)"),
    ("Sitao|String Beans", "String Beans (Sitao)"),
    ("Pechay Tagalog", "Pechay Tagalog"),
    ("Kalabasa|Squash", "Squash (Kalabasa)"),
    ("Talong|Eggplant", "Eggplant (Talong)"),
    ("Kamatis|Tomato", "Tomato (Kamatis)"),
    ("Cabbage|Repolyo", "Cabbage (Repolyo)"),
    ("Carrot|Karot", "Carrot (Karot)"),
    ("Baguio Beans|Habitchuelas", "Baguio Beans (Habitchuelas)"),
    ("Potato|Patatas", "White Potato (Patatas)"),
    ("Pechay Baguio", "Pechay Baguio"),
    ("Sayote|Chayote", "Chayote (Sayote)"),
    ("Calamansi", "Calamansi"),
    ("Lakatan", "Banana (Lakatan)"),
    ("Latundan", "Banana (Latundan)"),
    ("Papaya", "Papaya"),
    ("Mango", "Mango (Carabao)"),
    ("Sugar Refined", "Sugar (Refined)"),
    ("Sugar Washed", "Sugar (Washed)"),
    ("Sugar Brown", "Sugar (Brown)"),
    ("Palm Oil - 350ml", "Palm Oil (350ml)"),
    ("Palm Oil - 1L", "Palm Oil (1L)"),
    ("Coconut Oil - 350ml", "Coconut Oil (350ml)"),
    ("Coconut Oil - 1L", "Coconut Oil (1L)"),
    ("Local Red Onion", "Local Red Onion (Sibuyas)"),
    ("Imported Red Onion", "Imported Red Onion (Sibuyas)"),
    ("Local White Onion", "Local White Onion (Sibuyas)"),
    ("Imported White Onion", "Imported White Onion (Sibuyas)"),
    ("Imported Garlic", "Imported Garlic (Bawang)"),
    ("Local Garlic", "Local Garlic (Bawang)"),
    ("Ginger|Luya", "Ginger (Luya)"),
    ("Chili|Siling", "Chili (Siling Labuyo)"),
]

def map_canonical_ncr(raw):
    s = str(raw)
    # Match last occurring canonical commodity (closest to the price column)
    best_pos, best_name = -1, None
    for pat, label in CANONICAL_NCR:
        for m in re.finditer(pat, s, re.IGNORECASE):
            if m.start() > best_pos:
                best_pos = m.start()
                best_name = label
    return best_name

ncr = pd.read_csv("all_ncr_summary_2022.csv")
ncr["commodity"] = ncr["commodity"].apply(map_canonical_ncr)
ncr = ncr.dropna(subset=["commodity"])
ncr["unit"] = np.where(ncr["commodity"] == "Egg (Medium)", "PHP/piece",
              np.where(ncr["commodity"].str.contains("Oil"), "PHP/bottle", "PHP/kg"))

ncr_egg = (ncr["commodity"] == "Egg (Medium)") & (ncr["high"] > 20)
ncr.loc[ncr_egg, "high"] = ncr.loc[ncr_egg, "low"]

ncr = ncr.drop_duplicates(subset=["date", "group", "commodity", "source_file"]).sort_values(["date", "group", "commodity"])
ncr.to_csv("all_ncr_summary_2022.csv", index=False, encoding="utf-8-sig")

print("Final all_markets_2022.csv:", mkt.shape, "| Digits in commodity:", mkt["commodity"].str.contains(r"\d").sum())
print("Final all_ncr_summary_2022.csv:", ncr.shape, "| Unique commodities:", ncr["commodity"].nunique())