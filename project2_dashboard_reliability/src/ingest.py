"""Load City of Melbourne extracts into DuckDB and record a source manifest.

The manifest (file hash, size, row count, portal metadata dates) is the version
stamp for every downstream number: a KPI is traceable to exactly one file hash.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "com_requests.duckdb"

SOURCES = {
    "A": ("customer-service-requests-with-resolution-time", "raw_requests_a"),
    "B": ("customer-service-requests-with-digital-channel-information", "raw_requests_b"),
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    con = duckdb.connect(str(DB))
    manifest = []
    for code, (slug, table) in SOURCES.items():
        csv = RAW / f"{slug}.csv"
        meta = json.loads((RAW / f"{slug}.metadata.json").read_text(encoding="utf-8"))["metas"]["default"]
        df = pd.read_csv(csv, dtype=str, encoding="utf-8-sig", keep_default_na=False, na_values=[""])
        # the source has no request identifier; keep file order as a surrogate key
        df.insert(0, "source_row", range(1, len(df) + 1))
        con.register("df", df)
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM df")
        con.unregister("df")
        manifest.append(dict(
            dataset=code, slug=slug, file=csv.name, sha256=_sha256(csv), bytes=csv.stat().st_size,
            rows=len(df), portal_records_count=meta.get("records_count"),
            portal_modified=meta.get("modified"), portal_data_processed=meta.get("data_processed"),
            file_downloaded=datetime.fromtimestamp(csv.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            publisher_description=meta.get("description", "").replace("<br/>", " ")[:600],
        ))
        print(f"{table:18s} {len(df):>7,d} rows  sha256 {manifest[-1]['sha256'][:12]}")
    m = pd.DataFrame(manifest)
    con.register("m", m)
    con.execute("CREATE OR REPLACE TABLE gov_source_manifest AS SELECT * FROM m")
    con.close()


if __name__ == "__main__":
    main()
