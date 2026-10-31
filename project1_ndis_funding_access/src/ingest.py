"""Load the raw NDIA files into DuckDB raw tables, without changing values.

Every raw table keeps the source file name and the raw text of masked cells,
so staging can decide how to treat them and reconciliation can trace back.
"""
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "ndis.duckdb"

SDA_XLSX_TABLES = {
    # sheet -> (table name, number of label columns kept as text)
    "Table P.1": "raw_sda_p1_district",       # participants SDA in use / eligible by service district
    "Table P.4": "raw_sda_p4_dwellings_type",  # enrolled dwellings by SA4 and building type
    "Table P.6": "raw_sda_p6_dwellings_res",   # enrolled dwellings by SA4 and max residents
    "Table P.8": "raw_sda_p8_unfinished",      # unfinished new-build dwellings by SA4
    "Table P.9": "raw_sda_p9_need_sa4",        # participants with SDA need by status and SA4
}


def _read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    df["source_file"] = path.name
    return df


def _read_util_xlsx(path: Path) -> pd.DataFrame:
    # December 2024 utilisation was published as XLSX under a .csv link; values are fractions.
    df = pd.read_excel(path, dtype={"Utlstn": float})
    df["RprtDt"] = pd.to_datetime(df["RprtDt"]).dt.strftime("%d%b%Y").str.upper()
    df["Utlstn"] = df["Utlstn"].map(lambda v: "" if pd.isna(v) else f"{round(v * 100)}%")
    df = df.astype(str).replace({"nan": None, "": None})
    df["source_file"] = path.name
    return df


def _read_sda_table(xlsx: Path, sheet: str) -> pd.DataFrame:
    df = pd.read_excel(xlsx, sheet_name=sheet, header=None, dtype=str)
    title = df.iloc[0, 0]
    header = [str(h).strip() for h in df.iloc[1].tolist()]
    body = df.iloc[2:].copy()
    body.columns = header
    body = body.loc[:, [c for c in body.columns if c and c != "nan"]]
    body = body.dropna(how="all")
    first = body.columns[0]
    # notes/footnotes below the table start with text in col 0 and nothing else
    body = body[body.iloc[:, 1:].notna().any(axis=1)]
    body.insert(0, "table_title", title)
    body["source_file"] = xlsx.name
    return body.rename(columns={first: "region"})


def main() -> None:
    con = duckdb.connect(str(DB))
    pb = pd.concat([_read_csv(p) for p in sorted(RAW.glob("PB_*.csv"))], ignore_index=True)
    ut_parts = [_read_csv(p) for p in sorted(RAW.glob("UT_*.csv"))]
    ut_parts += [_read_util_xlsx(p) for p in sorted(RAW.glob("UT_*.xlsx"))]
    for p in ut_parts:
        p.rename(columns={"suppclass": "SuppClass"}, inplace=True)
    ut = pd.concat(ut_parts, ignore_index=True)
    sda = _read_csv(RAW / "SDA_participants.csv")

    con.register("pb_df", pb)
    con.execute("CREATE OR REPLACE TABLE raw_plan_budgets AS SELECT * FROM pb_df")
    con.register("ut_df", ut)
    con.execute("CREATE OR REPLACE TABLE raw_utilisation AS SELECT * FROM ut_df")
    con.register("sda_df", sda)
    con.execute("CREATE OR REPLACE TABLE raw_sda_participants AS SELECT * FROM sda_df")

    xlsx = RAW / "SDA_dwellings_demand.xlsx"
    for sheet, table in SDA_XLSX_TABLES.items():
        df = _read_sda_table(xlsx, sheet)
        con.register("t", df)
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM t")
        con.unregister("t")

    for t in ["raw_plan_budgets", "raw_utilisation", "raw_sda_participants", *SDA_XLSX_TABLES.values()]:
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"{t:32s} {n:>9,d} rows")
    con.close()


if __name__ == "__main__":
    main()
