"""Download the NDIA datasets used by Project 1 into data/raw/.

Media IDs are those listed on https://dataresearch.ndis.gov.au/datasets/participant-datasets
(checked 30-Oct-2026). NDIA replaces files each quarter: update the IDs for a new release.
"""
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
BASE = "https://dataresearch.ndis.gov.au/media/{}/download?attachment"
FILES = {
    "PB_2025Q1_Sep24.csv": 4144, "PB_2025Q2_Dec24.csv": 4233, "PB_2025Q3_Mar25.csv": 4314,
    "PB_2025Q4_Jun25.csv": 4366, "PB_2026Q1_Sep25.csv": 4396, "PB_2026Q2_Dec25.csv": 4484,
    "PB_2026Q3_Mar26.csv": 4528, "PB_2026Q4_Jun26.csv": 4573,
    "UT_2025Q1_Sep24.csv": 4145, "UT_2025Q2_Dec24.xlsx": 4253,  # published as XLSX behind a CSV link
    "UT_2025Q3_Mar25.csv": 4315, "UT_2025Q4_Jun25.csv": 4367, "UT_2026Q1_Sep25.csv": 4398,
    "UT_2026Q2_Dec25.csv": 4485, "UT_2026Q3_Mar26.csv": 4529, "UT_2026Q4_Jun26.csv": 4574,
    "SDA_participants.csv": 4250, "SDA_dwellings_demand.xlsx": 4248,
    "rules_plan_budgets.docx": 4260, "rules_utilisation.docx": 4270,
    "rules_sda_participants.docx": 4268, "commonly_used_data_terms.docx": 1965,
}

RAW.mkdir(parents=True, exist_ok=True)
for name, media in FILES.items():
    req = urllib.request.Request(BASE.format(media), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r, open(RAW / name, "wb") as f:
        f.write(r.read())
    print("downloaded", name)
