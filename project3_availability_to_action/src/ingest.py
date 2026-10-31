"""Load the FreshRetailNet-50K parquet files into DuckDB and record a source manifest.

The manifest (file hash, size, row count, date range) is the version stamp for every downstream
number: a ranking or a backtest result is traceable to exactly one pair of file hashes.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "a2a.duckdb"
PARAMS = json.loads((ROOT / "config" / "params.json").read_text(encoding="utf-8"))

SOURCES = {"train": "train.parquet", "eval": "eval.parquet"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    missing = [f for f in SOURCES.values() if not (RAW / f).exists()]
    if missing:
        raise SystemExit(f"missing {missing}: run scripts/download_data.py")
    con = duckdb.connect(str(DB))
    con.execute("SET enable_progress_bar = false")
    manifest = []
    for split, file in SOURCES.items():
        path = RAW / file
        con.execute(f"CREATE OR REPLACE TABLE raw_{split} AS SELECT * FROM read_parquet('{path.as_posix()}')")
        rows, d0, d1, series = con.execute(
            f"SELECT count(*), min(dt), max(dt), count(DISTINCT (store_id, product_id)) FROM raw_{split}").fetchall()[0]
        manifest.append(dict(
            split=split, file=file, sha256=_sha256(path), bytes=path.stat().st_size, rows=rows, series=series,
            first_date=d0, last_date=d1, publisher="Dingdong-Inc (Hugging Face)", licence="CC BY 4.0",
            source_url=f"https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K/blob/main/data/{file}",
            file_downloaded=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
        ))
        print(f"raw_{split:6s} {rows:>9,d} rows  {series:,d} series  {d0} to {d1}  sha256 {manifest[-1]['sha256'][:12]}")
    m = pd.DataFrame(manifest)
    con.register("m", m)
    con.execute("CREATE OR REPLACE TABLE gov_source_manifest AS SELECT * FROM m")
    con.close()


if __name__ == "__main__":
    main()
